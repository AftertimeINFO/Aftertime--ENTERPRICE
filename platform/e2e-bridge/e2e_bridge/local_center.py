"""In-process launcher of the stand's E2E orchestrator (``run-all.ps1``): the same API as ``Center``.

It replaces an external control center. One stand, one run at a time: jobs queue and run strictly one after another.
``state()``/``job()`` give what the bridge reads from a control center (``running``, ``queue``, ``jobs``, ``history``;
``status``, ``lines``, ``next``, ``exit``). Jobs live in the bridge process: a bridge restart forgets the queue, and a run
that was already started keeps running as an ordinary process of the stand.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import threading
import time
from pathlib import Path
from typing import Any

from e2e_bridge.center import CenterError

_NAME = re.compile(r"^[\w\-.]+$")
_NO_WINDOW = 0x08000000  # CREATE_NO_WINDOW


def find_pwsh() -> str:
    """Resolved on every start: the packaged PowerShell moves its folder on update."""
    return shutil.which("pwsh") or r"C:\Program Files\PowerShell\7\pwsh.exe"


class _Job:
    def __init__(self, job_id: str, title: str, args: list[str]) -> None:
        self.id = job_id
        self.title = title
        self.args = args
        self.status = "queued"  # queued | running | passed | failed | stopped
        self.created = time.time()
        self.started: float | None = None
        self.finished: float | None = None
        self.exit: int | None = None
        self.lines: list[str] = []
        self.proc: subprocess.Popen | None = None
        self.lock = threading.Lock()

    def add(self, text: str) -> None:
        with self.lock:
            self.lines.append(text)

    def info(self, start: int = 0, with_lines: bool = True) -> dict[str, Any]:
        with self.lock:
            data: dict[str, Any] = {
                "id": self.id, "title": self.title, "status": self.status, "created": self.created,
                "started": self.started, "finished": self.finished, "exit": self.exit, "next": len(self.lines),
            }
            if with_lines:
                data["lines"] = self.lines[start:]
            return data


class LocalCenter:
    def __init__(self, run_all: Path, jobs_dir: Path | None = None, reset_script: Path | None = None) -> None:
        self.run_all = Path(run_all)
        if not self.run_all.is_file():
            raise CenterError(f"run-all.ps1 не знайдено: {self.run_all}")
        self.root = self.run_all.parent
        self.jobs_dir = Path(jobs_dir) if jobs_dir else None
        # The script that resets the test clients after a forced stop (a killed runner cannot run its finally blocks).
        self.reset_script = reset_script or self.root / "vanessa" / "reset-ski-test-client.ps1"
        self._jobs: dict[str, _Job] = {}
        self._order: list[str] = []
        self._queue: list[_Job] = []
        self._current: _Job | None = None
        self._seq = 0
        self._lock = threading.Lock()
        self._worker: threading.Thread | None = None

    # ------------------------------------------------------------ same API as Center
    def catalog(self) -> dict[str, Any]:
        """Suites -> cases of the stand (``suites.json`` + case manifests): what the old control center published."""

        def load(path: Path) -> Any:
            try:
                return json.loads(path.read_text(encoding="utf-8-sig"))
            except (OSError, ValueError):
                return None

        cfg = load(self.root / "suites.json") or {}
        manifest: dict[str, dict[str, Any]] = {}
        for name in ("suite-cases.json", "payment-cases.json", "chain-cases.json"):
            for case in (load(self.root / "vanessa" / "data" / name) or {}).get("cases", []):
                manifest[case["id"]] = case
        suites = []
        for suite in cfg.get("suites", []):
            if suite.get("caseSource"):
                source = (load(self.root / suite["caseSource"]) or {}).get("cases", [])
                for case in source:
                    manifest.setdefault(case["id"], case)
                ids = [case["id"] for case in source]
            else:
                ids = list(suite.get("cases", []))
            cases = []
            for case_id in ids:
                item = manifest.get(case_id, {})
                cases.append({"id": case_id, "title": item.get("title") or item.get("expected") or case_id,
                              "number": item.get("number"), "feature": item.get("feature")})
            suites.append({"id": suite["id"], "title": suite.get("title") or suite["id"], "needs": suite.get("needs", {}),
                           "cases": cases})
        return {"suites": suites, "groups": cfg.get("groups", {})}

    def state(self) -> dict[str, Any]:
        with self._lock:
            state = {
                "running": self._current.id if self._current else None,
                "queue": [job.id for job in self._queue],
                "jobs": [self._jobs[i].info(with_lines=False) for i in self._order[-15:][::-1]],
                "history": [],
            }
        state["catalog"] = self.catalog()
        return state

    def busy(self) -> dict[str, Any]:
        state = self.state()
        return {"running": state["running"], "queue": state["queue"]}

    def run(
        self,
        cases: list[str],
        title: str,
        suites: list[str] | None = None,
        dry_run: bool = False,
        stop_on_failure: bool = False,
    ) -> str:
        cases = [c for c in cases if _NAME.match(c)]
        suites = [s for s in (suites or []) if _NAME.match(s)]
        if not cases and not suites:
            raise CenterError("refusing to start a run without suites or cases")
        # -NonInteractive: a missing mandatory parameter must fail the job at once, not wait for input in a hidden process.
        args = [find_pwsh(), "-NoProfile", "-NonInteractive", "-File", str(self.run_all)]
        args += ["-Case", ",".join(cases)] if cases else ["-Suite", ",".join(suites)]
        if dry_run:
            args.append("-DryRun")
        if stop_on_failure:
            args.append("-StopOnFailure")
        with self._lock:
            self._seq += 1
            job_id = time.strftime("%Y%m%d-%H%M%S") + "-%02d" % self._seq
            job = _Job(job_id, title[:200], args)
            self._jobs[job_id] = job
            self._order.append(job_id)
            self._queue.append(job)
            if self._worker is None or not self._worker.is_alive():
                self._worker = threading.Thread(target=self._work, name="local-center", daemon=True)
                self._worker.start()
        return job_id

    def job(self, job_id: str, start: int = 0) -> dict[str, Any]:
        job = self._jobs.get(job_id)
        if job is None:
            raise CenterError(f"no such job: {job_id}")
        return job.info(start)

    def stop(self, job_id: str) -> bool:
        job = self._jobs.get(job_id)
        if job is None:
            return False
        if job.status == "queued":
            with self._lock:
                if job in self._queue:
                    self._queue.remove(job)
            job.status, job.finished = "stopped", time.time()
            return True
        if job.status == "running" and job.proc is not None:
            job.status = "stopped"
            job.add("--- stop requested: the process tree is killed, the test clients are reset")
            subprocess.call(["taskkill", "/PID", str(job.proc.pid), "/T", "/F"], creationflags=_NO_WINDOW)
            if self.reset_script.is_file():
                subprocess.call([find_pwsh(), "-NoProfile", "-File", str(self.reset_script)], cwd=self.root, creationflags=_NO_WINDOW)
            self._restore_printer()
            return True
        return False

    def _restore_printer(self) -> None:
        """run-all.ps1 remembers the user's default printer before tests switch it; a killed run cannot put it back."""
        saved = self.root / "vanessa" / "logs" / "default-printer.txt"
        if not saved.is_file():
            return
        name = saved.read_text(encoding="utf-8-sig").strip()
        if not name:
            return
        quoted = name.replace("'", "''")
        command = (f"$p = Get-CimInstance Win32_Printer | Where-Object Name -eq '{quoted}'; "
                   "if ($p) { Invoke-CimMethod -InputObject $p -MethodName SetDefaultPrinter | Out-Null }")
        subprocess.call([find_pwsh(), "-NoProfile", "-Command", command], creationflags=_NO_WINDOW)

    # ------------------------------------------------------------ worker
    def _work(self) -> None:
        while True:
            with self._lock:
                if not self._queue:
                    self._worker = None
                    return
                job = self._queue.pop(0)
                self._current = job
            try:
                self._execute(job)
            finally:
                with self._lock:
                    self._current = None

    def _execute(self, job: _Job) -> None:
        job.status, job.started = "running", time.time()
        job.add("> " + " ".join(job.args))
        try:
            job.proc = subprocess.Popen(
                job.args, cwd=self.root, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                creationflags=_NO_WINDOW,
            )
            log = None
            if self.jobs_dir:
                self.jobs_dir.mkdir(parents=True, exist_ok=True)
                log = open(self.jobs_dir / f"{job.id}.log", "w", encoding="utf-8")
            try:
                for raw in iter(job.proc.stdout.readline, b""):
                    text = raw.decode("utf-8", errors="replace").rstrip("\r\n")
                    job.add(text)
                    if log:
                        log.write(text + "\n")
                        log.flush()
            finally:
                if log:
                    log.close()
            job.exit = job.proc.wait()
            outcome = job.status if job.status == "stopped" else ("passed" if job.exit == 0 else "failed")
        except OSError as exc:
            job.add(f"ERROR: {exc}")
            outcome = "failed"
        job.finished = time.time()
        job.add(f"--- finished: {outcome} (exit {job.exit})")
        job.status = outcome  # last: the bridge stops reading lines as soon as it sees a final status
