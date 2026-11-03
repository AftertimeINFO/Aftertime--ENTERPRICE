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
_PATH_RE = re.compile(r"[A-Za-z]:\\[^\s\"'()]+")


@dataclass
class Settings:
    center_url: str
    run_root: Path
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


def load_bindings(config_dir: Path) -> dict[str, Binding]:
    path = config_dir / "e2e" / "bindings.json"
    data = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    return {key: Binding.from_config(key, value) for key, value in data.items() if isinstance(value, dict)}


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
        sleep(settings.poll_seconds)
    folder = find_run_folder(lines, settings.run_root, binding.run_folder_mask)
    outcome = status
    final = folder_states(binding, folder, outcome) if folder else phase_states(binding.phases, live_proofs, outcome)
    for item in changes(states, final):
        sink({"type": "phase", "job": job_id, **item})
    result = {"type": "finished", "job": job_id, "status": outcome,
              "folder": str(folder) if folder else None, "states": final}
    sink(result)
    return result
