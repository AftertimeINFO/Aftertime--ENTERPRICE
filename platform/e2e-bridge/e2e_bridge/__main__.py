"""E2E bridge: run test cases bound to BPMN processes through a control center.

    python -m e2e_bridge status
    python -m e2e_bridge replay BINDING RUN_FOLDER [--outcome passed|failed|stopped]
    python -m e2e_bridge run BINDING [--wait-free]
    python -m e2e_bridge follow BINDING JOB_ID

Settings come from ``$AFTERTIME_CONFIG_DIR/e2e`` (``bindings.json``, ``bridge.env``);
without the variable, ``../Aftertime-config`` next to the repository root.
Every event is printed as one JSON line.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

from e2e_bridge.center import Center, CenterError
from e2e_bridge.runner import Settings, folder_states, follow, load_bindings

REPO_ROOT = Path(__file__).resolve().parents[3]


def config_dir() -> Path:
    value = os.environ.get("AFTERTIME_CONFIG_DIR")
    return Path(value) if value else REPO_ROOT.parent / "Aftertime-config"


def emit(event: dict) -> None:
    print(json.dumps(event, ensure_ascii=False, default=str), flush=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("status")
    replay = commands.add_parser("replay")
    replay.add_argument("binding")
    replay.add_argument("folder", type=Path)
    replay.add_argument("--outcome", choices=["passed", "failed", "stopped"])
    run = commands.add_parser("run")
    run.add_argument("binding")
    run.add_argument("--wait-free", action="store_true", help="чекати, поки стенд звільниться")
    follow_cmd = commands.add_parser("follow")
    follow_cmd.add_argument("binding")
    follow_cmd.add_argument("job")
    args = parser.parse_args(argv)
    try:
        return _dispatch(args)
    except CenterError as exc:
        emit({"type": "error", "message": f"Центр E2E недоступний: {exc}"})
        return 3


def _dispatch(args: argparse.Namespace) -> int:
    directory = config_dir()
    settings = Settings.load(directory)
    bindings = load_bindings(directory)
    center = Center(settings.center_url)

    if args.command == "status":
        emit({"type": "center", "url": settings.center_url, **center.busy(),
              "bindings": {key: value.cases for key, value in bindings.items()}})
        return 0

    binding = bindings.get(args.binding)
    if binding is None:
        emit({"type": "error", "message": f"Прив'язку «{args.binding}» не знайдено в {directory}"})
        return 2

    if args.command == "replay":
        outcome = args.outcome
        summary = args.folder / "summary.json"
        if outcome is None and summary.exists():
            data = json.loads(summary.read_text(encoding="utf-8-sig"))
            data = data[0] if isinstance(data, list) and data else data
            outcome = data.get("status") if isinstance(data, dict) else None
        emit({"type": "finished", "status": outcome, "folder": str(args.folder),
              "states": folder_states(binding, args.folder, outcome)})
        return 0

    if args.command == "run":
        busy = center.busy()
        while args.wait_free and (busy["running"] or busy["queue"]):
            emit({"type": "waiting", **busy})
            time.sleep(settings.poll_seconds * 6)
            busy = center.busy()
        job_id = center.run(binding.cases, f"Aftertime · {binding.title}")
        emit({"type": "queued", "job": job_id, "binding": binding.id, "cases": binding.cases,
              "ahead": len(busy["queue"]) + (1 if busy["running"] else 0)})
    else:
        job_id = args.job
    result = follow(center, settings, binding, job_id, emit)
    return 0 if result["status"] == "passed" else 1


if __name__ == "__main__":
    sys.exit(main())
