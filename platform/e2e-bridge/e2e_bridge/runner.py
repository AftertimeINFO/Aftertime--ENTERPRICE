"""Start a bound case in the control center and follow it phase by phase."""

from __future__ import annotations

import fnmatch
import json
import os
import re
import subprocess
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from e2e_bridge.center import FINISHED, Center
from e2e_bridge.phases import changes, folder_entries, phase_states, read_proofs

Sink = Callable[[dict[str, Any]], None]
MAIN_SCREEN = "screen"


@dataclass(frozen=True)
class Screen:
    """A window recorded during a run: the machine under test, or an application around it."""

    id: str
    title: str
    command: str
    seconds: float = 3


def load_screens(values: dict[str, str]) -> list[Screen]:
    """``SCREEN_COMMAND`` is the main window; ``SCREENS_FILE`` (JSON list) adds the others::

        [{"id": "web", "title": "Веб-застосунок", "command": "...", "seconds": 2}]
    """
    seconds = float(values.get("SCREEN_SECONDS", 3))
    screens = []
    if values.get("SCREEN_COMMAND"):
        screens.append(Screen(MAIN_SCREEN, values.get("SCREEN_TITLE") or "Екран машини під тестом",
                              values["SCREEN_COMMAND"], seconds))
    path = Path(values["SCREENS_FILE"]) if values.get("SCREENS_FILE") else None
    if path and path.exists():
        for item in json.loads(path.read_text(encoding="utf-8-sig")):
            if isinstance(item, dict) and item.get("id") and item.get("command") and item["id"] != MAIN_SCREEN:
                screens.append(Screen(str(item["id"]), str(item.get("title") or item["id"]), str(item["command"]),
                                      float(item.get("seconds", seconds))))
    return screens
_PATH_RE = re.compile(r"[A-Za-z]:\\[^\s\"'()]+")


@dataclass
class Settings:
    center_url: str
    run_root: Path
    aftertime_url: str = ""
    results_root: Path | None = None
    packs_dir: Path | None = None
    screen_command: str = ""
    screen_seconds: float = 3
    screens: list[Screen] = field(default_factory=list)
    live_command: str = ""
    poll_seconds: float = 5
    live_seconds: float = 20

    @classmethod
    def load(cls, config_dir: Path) -> "Settings":
        values = dict(os.environ)
        env_file = config_dir / "e2e" / "bridge.env"
        if env_file.exists():
            for line in env_file.read_text(encoding="utf-8").splitlines():
                if "=" in line and not line.lstrip().startswith("#"):
                    key, value = line.split("=", 1)
                    values.setdefault(key.strip(), value.strip())
        return cls(
            center_url=values.get("CENTER_URL", ""),
            run_root=Path(values.get("RUN_ROOT", ".")),
            aftertime_url=values.get("AFTERTIME_API_URL", "http://127.0.0.1:8000/api/v1"),
            results_root=Path(values["RESULTS_ROOT"]) if values.get("RESULTS_ROOT") else None,
            packs_dir=Path(values["PACKS_DIR"]) if values.get("PACKS_DIR") else None,
            screen_command=values.get("SCREEN_COMMAND", ""),
            screen_seconds=float(values.get("SCREEN_SECONDS", 3)),
            screens=load_screens(values),
            live_command=values.get("LIVE_COMMAND", ""),
            poll_seconds=float(values.get("POLL_SECONDS", 5)),
            live_seconds=float(values.get("LIVE_SECONDS", 20)),
        )


@dataclass
class Binding:
    id: str
    title: str
    phases: list[str]
    cases: list[str]
    run_folder_mask: str
    proof_pattern: str
    error_pattern: str | None = None
    live: bool = False
    extra: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_config(cls, binding_id: str, data: dict[str, Any]) -> "Binding":
        runner = data.get("runner", {})
        return cls(
            id=binding_id,
            title=data.get("title", binding_id),
            phases=list(data.get("phases", [])),
            cases=list(runner.get("cases", [])),
            run_folder_mask=runner.get("runFolderMask", "*"),
            proof_pattern=runner.get("proofPattern", r"-(?P<phase>[A-Za-z0-9_]+)\.json$"),
            error_pattern=runner.get("errorPattern"),
            live=bool(runner.get("live", False)),
            extra=runner,
        )


def _read_bindings(path: Path) -> dict[str, dict[str, Any]]:
    data = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    return {key: value for key, value in data.items() if isinstance(value, dict)}


def pack_bindings(packs_dir: Path | None) -> dict[str, dict[str, Any]]:
    """Bindings shipped by model packs (directories with aftertime-pack.json)."""
    found: dict[str, dict[str, Any]] = {}
    if not packs_dir or not packs_dir.is_dir():
        return found
    for path in sorted(item for item in packs_dir.iterdir() if (item / "aftertime-pack.json").exists()):
        try:
            manifest = json.loads((path / "aftertime-pack.json").read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if manifest.get("bindings"):
            for key, value in _read_bindings(path / manifest["bindings"]).items():
                found.setdefault(key, value)
    return found


def load_bindings(config_dir: Path, packs_dir: Path | None = None) -> dict[str, Binding]:
    """Pack bindings with the stand settings on top, the same merge as the Aftertime core."""
    merged = pack_bindings(packs_dir)
    for key, value in _read_bindings(config_dir / "e2e" / "bindings.json").items():
        base = merged.get(key, {})
        merged[key] = {**base, **value, "runner": {**base.get("runner", {}), **value.get("runner", {})}}
    return {key: Binding.from_config(key, value) for key, value in merged.items()}


def find_run_folder(lines: list[str], run_root: Path, mask: str) -> Path | None:
    """The last existing run folder named in the job log."""
    found = None
    root = str(run_root).rstrip("\\/").lower()
    for line in lines:
        for raw in _PATH_RE.findall(line):
            path = Path(raw.rstrip(".,;:"))
            if str(path.parent).lower() == root and fnmatch.fnmatch(path.name, mask) and path.is_dir():
                found = path
    return found


def folder_states(binding: Binding, folder: Path, outcome: str | None) -> list[dict[str, Any]]:
    proofs = read_proofs(folder_entries(folder), binding.proof_pattern, binding.error_pattern)
    return phase_states(binding.phases, proofs, outcome)


def live_entries(command: str, since: datetime) -> list[tuple[str, str]]:
    """Proof files of the running case, from a settings-provided command.

    The command prints a JSON list of ``{"name", "text"}`` written after
    ``BRIDGE_SINCE`` (ISO time); errors only mean no live data this round.
    """
    env = {**os.environ, "BRIDGE_SINCE": since.isoformat()}
    try:
        result = subprocess.run(command, shell=True, capture_output=True, text=True,
                                encoding="utf-8", errors="replace", timeout=60, env=env)
        data = json.loads(result.stdout.strip() or "[]")
    except (subprocess.SubprocessError, json.JSONDecodeError):
        return []
    if isinstance(data, dict):
        data = [data]
    return [(item.get("name", ""), item.get("text", "")) for item in data if isinstance(item, dict)]


def follow(
    center: Center,
    settings: Settings,
    binding: Binding,
    job_id: str,
    sink: Sink,
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
    should_stop: Callable[[], bool] = lambda: False,
) -> dict[str, Any]:
    """Stream log lines and phase changes of one job until it finishes.

    Live proofs that already exist when following starts belong to earlier runs
    (the stand keeps them, and its clock may be shifted), so they are ignored;
    a job joined mid-run therefore shows its earlier phases only at the end.
    """
    since = datetime.now(timezone.utc)
    use_live = binding.live and bool(settings.live_command)
    baseline = {name for name, _ in live_entries(settings.live_command, since)} if use_live else set()
    lines: list[str] = []
    position = 0
    states = phase_states(binding.phases, {}, None)
    live_proofs: dict = {}
    next_live = clock()
    last_status = None
    last_progress = None
    stop_sent = False
    sink({"type": "states", "job": job_id, "states": states})
    while True:
        job = center.job(job_id, position)
        new_lines = job.get("lines") or []
        lines.extend(new_lines)
        position = job.get("next", position + len(new_lines))
        for line in new_lines:
            sink({"type": "log", "job": job_id, "line": line})
        status = job.get("status")
        if status in FINISHED:
            break
        if not stop_sent and should_stop():
            stop_sent = True
            sink({"type": "log", "job": job_id, "line": "Aftertime: зупинку запитано, зупиняю завдання в центрі"})
            center.stop(job_id)
        if status == "running" and use_live and clock() >= next_live:
            next_live = clock() + settings.live_seconds
            fresh = [entry for entry in live_entries(settings.live_command, since) if entry[0] not in baseline]
            live_proofs = read_proofs(fresh, binding.proof_pattern, binding.error_pattern)
        current = phase_states(binding.phases, live_proofs, None)
        for item in changes(states, current):
            sink({"type": "phase", "job": job_id, **item})
        states = current
        if status != last_status:
            sink({"type": "status", "job": job_id, "status": status, "progress": job.get("progress")})
            last_status = status
        if job.get("progress") and job.get("progress") != last_progress:
            last_progress = job.get("progress")
            sink({"type": "progress", "job": job_id, "progress": last_progress})
        sleep(settings.poll_seconds)
    folder = find_run_folder(lines, settings.run_root, binding.run_folder_mask)
    outcome = status
    # A failed runner may not copy its proofs into the run folder, so the folder only adds to
    # what was seen live and never erases it.
    folder_proofs = read_proofs(folder_entries(folder), binding.proof_pattern, binding.error_pattern) if folder else {}
    final = phase_states(binding.phases, {**live_proofs, **folder_proofs}, outcome)
    for item in changes(states, final):
        sink({"type": "phase", "job": job_id, **item})
    result = {"type": "finished", "job": job_id, "status": outcome,
              "folder": str(folder) if folder else None, "states": final}
    sink(result)
    return result
