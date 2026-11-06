"""Serve Aftertime: claim requested runs, execute them, report progress back."""

from __future__ import annotations

import json
import re
import threading
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from typing import Any, Callable

from e2e_bridge.center import Center, CenterError
from e2e_bridge.phases import PASSED, Proof, changes, phase_states, read_proofs
from e2e_bridge.results import Uploader, launches_since, newest_launch_for
from e2e_bridge.runner import MAIN_SCREEN, Binding, Screen, Settings, follow, live_entries
from e2e_bridge.screen import ScreenRecorder


class Aftertime:
    def __init__(self, url: str, timeout: float = 30) -> None:
        self.url = url.rstrip("/")
        self.timeout = timeout

    def _call(self, method: str, path: str, body: dict | None = None) -> Any:
        request = urllib.request.Request(
            f"{self.url}{path}",
            method=method,
            data=json.dumps(body, default=str).encode("utf-8") if body is not None else None,
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(request, timeout=self.timeout) as response:
            raw = response.read()
            return json.loads(raw) if raw else None

    def claim(self) -> dict[str, Any] | None:
        return self._call("POST", "/lab/e2e-runs/claim")

    def push_state(self, state: dict[str, Any]) -> None:
        self._call("POST", "/lab/center/state", state)

    def frame(self, run_id: str, jpeg: bytes, captured_at: str, stream: str = MAIN_SCREEN, title: str = "") -> None:
        request = urllib.request.Request(
            f"{self.url}/lab/e2e-runs/{run_id}/frames", method="POST", data=jpeg,
            headers={"Content-Type": "image/jpeg", "X-Captured-At": captured_at, **_stream_headers(stream, title)},
        )
        with urllib.request.urlopen(request, timeout=self.timeout) as response:
            response.read()

    def recording(self, run_id: str, archive: Any, started_at: str, stream: str = MAIN_SCREEN, title: str = "") -> None:
        """Upload the zip of screen frames of a run; Aftertime packs it into a video."""
        request = urllib.request.Request(
            f"{self.url}/lab/e2e-runs/{run_id}/recording", method="POST", data=archive.read_bytes(),
            headers={"Content-Type": "application/zip", "X-Recording-Started-At": started_at,
                     **_stream_headers(stream, title)},
        )
        with urllib.request.urlopen(request, timeout=600) as response:
            response.read()

    def report(self, run_id: str, events: list[dict[str, Any]]) -> dict[str, Any]:
        return self._call("POST", f"/lab/e2e-runs/{run_id}/events", {"events": events})

    def start_case(self, run_id: str, case: str, binding: str) -> dict[str, Any]:
        """A test of the suite run ``run_id`` begins: Aftertime opens a run of its bound process."""
        return self._call("POST", f"/lab/e2e-runs/{run_id}/cases", {"case": case, "binding": binding})


def _stream_headers(stream: str, title: str) -> dict[str, str]:
    headers = {"X-Stream": stream}
    if title:
        # HTTP headers are Latin-1: the title travels percent-encoded.
        headers["X-Stream-Title"] = urllib.parse.quote(title)
    return headers


class AftertimeSink:
    """Buffers log lines, sends everything else at once, remembers stop requests."""

    def __init__(self, aftertime: Aftertime, run_id: str, echo: Callable[[dict], None] | None = None) -> None:
        self.aftertime = aftertime
        self.run_id = run_id
        self.echo = echo
        self.buffer: list[dict[str, Any]] = []
        self.stop_requested = False

    def __call__(self, event: dict[str, Any]) -> None:
        if self.echo:
            self.echo(event)
        self.buffer.append(event)
        if event.get("type") != "log" or len(self.buffer) >= 20:
            self.flush()

    def flush(self) -> None:
        if not self.buffer:
            return
        events, self.buffer = self.buffer, []
        reply = self.aftertime.report(self.run_id, events) or {}
        self.stop_requested = self.stop_requested or bool(reply.get("stop_requested"))


def simulate(
    binding: Binding,
    job_id: str,
    sink: Callable[[dict], None],
    step_seconds: float = 3,
    fail_at: str | None = None,
    should_stop: Callable[[], bool] = lambda: False,
    sleep: Callable[[float], None] = time.sleep,
) -> dict[str, Any]:
    """Walk the phases without a test stand, for demonstrations and UI checks."""
    proofs: dict[str, Proof] = {}
    states = phase_states(binding.phases, proofs, None)
    sink({"type": "states", "job": job_id, "states": states})
    sink({"type": "status", "job": job_id, "status": "running"})
    outcome = PASSED
    for phase in binding.phases:
        sleep(step_seconds)
        if should_stop():
            outcome = "stopped"
            break
        failed = phase == fail_at
        proofs[phase] = Proof(phase, not failed, "simulation", "симульований збій" if failed else "")
        sink({"type": "log", "job": job_id, "line": f"[симуляція] фаза {phase}: {'збій' if failed else 'пройдено'}"})
        current = phase_states(binding.phases, proofs, None)
        for item in changes(states, current):
            sink({"type": "phase", "job": job_id, **item})
        states = current
        if failed:
            outcome = "failed"
            break
    final = phase_states(binding.phases, proofs, outcome)
    for item in changes(states, final):
        sink({"type": "phase", "job": job_id, **item})
    result = {"type": "finished", "job": job_id, "status": outcome, "folder": None, "states": final}
    sink(result)
    return result


class Screens:
    """The windows of a test recorded side by side; closing packs them all at once.

    ``only``: window ids the test is set to record (``runner.screens`` of its binding); None records them all.
    """

    def __init__(self, settings: Settings, sink: AftertimeSink, run_id: str, only: list[str] | None = None,
                 case: str = "") -> None:
        self.sink = sink
        screens = settings.screens or ([Screen(MAIN_SCREEN, "", settings.screen_command, settings.screen_seconds)]
                                       if settings.screen_command else [])
        if only is not None:
            screens = [screen for screen in screens if screen.id in only]
        env = {"BRIDGE_RECORDING_ID": run_id, **({"BRIDGE_CASE": case} if case else {})}
        self.recorders = [(screen, ScreenRecorder(
            screen.command,
            lambda jpeg, at, screen=screen: sink.aftertime.frame(run_id, jpeg, at, screen.id, screen.title),
            screen.seconds,
            send_recording=lambda archive, started, screen=screen: sink.aftertime.recording(
                run_id, archive, started, screen.id, screen.title),
            env=env,
        )) for screen in screens]

    def start(self) -> None:
        for _, recorder in self.recorders:
            recorder.start()

    def close(self) -> None:
        threads = [threading.Thread(target=recorder.close) for _, recorder in self.recorders]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        for screen, recorder in self.recorders:
            if recorder.started_at:
                self.sink({"type": "log", "line": f"Aftertime: вікно «{screen.title or screen.id}» — живих кадрів "
                                                  f"{recorder.frames}; запис для відео "
                                                  f"{'передано, пакується у фоні' if recorder.recorded else 'не отримано'}"})


CASE_LINE = re.compile(r"CASE\|(start|end)\|([A-Za-z0-9][\w.-]*)(?:\|(\w+))?")
CASE_OUTCOMES = {"passed": "passed", "failed": "failed", "skipped": "stopped", "timedOut": "failed", "interrupted": "stopped"}


def screens_of(binding: Binding) -> list[str] | None:
    """Windows a test records (``runner.screens`` of its binding); None: every window of the settings."""
    value = binding.extra.get("screens")
    return [str(item) for item in value] if isinstance(value, list) else None


class CaseRuns:
    """Each test of a suite run as its own Aftertime run, from the moment the stand says it begins.

    The stand prints ``CASE|start|<case>`` and ``CASE|end|<case>|<status>``; for the test between them
    the bridge keeps a run of its bound process with its own recording of the windows set for the test,
    its log and its phases (live proofs while it runs, the report afterwards).
    """

    def __init__(self, aftertime: Aftertime, parent_id: str, bindings: dict[str, Binding], settings: Settings,
                 echo: Callable[[dict], None] | None = None) -> None:
        self.aftertime = aftertime
        self.parent_id = parent_id
        self.settings = settings
        self.echo = echo
        # a test belongs to the binding made for it alone, else to the first binding that lists it
        self.by_case: dict[str, Binding] = {}
        for binding in sorted(bindings.values(), key=lambda item: len(item.cases)):
            for case in binding.cases:
                self.by_case.setdefault(case, binding)
        self.current: dict[str, Any] | None = None
        self.closing: list[threading.Thread] = []

    def line(self, line: str) -> None:
        match = CASE_LINE.search(line)
        if self.current and not (match and match.group(1) == "start"):
            self.current["sink"]({"type": "log", "line": line})
        if not match:
            return
        state, case, status = match.groups()
        if state == "start":
            self.finish("stopped")  # a test that never said it ended
            self.begin(case)
            if self.current:
                self.current["sink"]({"type": "log", "line": line})
        elif self.current and self.current["case"] == case:
            self.finish(CASE_OUTCOMES.get(status or "", "failed"))

    def begin(self, case: str) -> None:
        binding = self.by_case.get(case)
        if binding is None:
            return
        try:
            child = self.aftertime.start_case(self.parent_id, case, binding.id)
        except OSError as exc:
            if self.echo:
                self.echo({"type": "error", "message": f"Прогін тесту {case} не створено: {exc}"})
            return
        sink = AftertimeSink(self.aftertime, child["id"], self.echo)
        screens = Screens(self.settings, sink, child["id"], screens_of(binding), case)
        screens.start()
        use_live = binding.live and bool(self.settings.live_command)
        since = datetime.now(timezone.utc)
        self.current = {
            "case": case, "binding": binding, "sink": sink, "screens": screens, "since": since, "proofs": {},
            "use_live": use_live, "next_live": 0.0, "states": phase_states(binding.phases, {}, None),
            "baseline": {name for name, _ in live_entries(self.settings.live_command, since)} if use_live else set(),
        }
        sink({"type": "states", "states": self.current["states"]})

    def tick(self) -> None:
        """Live proofs of the running test, at most every ``live_seconds``."""
        current = self.current
        if not current or not current["use_live"] or time.monotonic() < current["next_live"]:
            return
        current["next_live"] = time.monotonic() + self.settings.live_seconds
        binding: Binding = current["binding"]
        fresh = [entry for entry in live_entries(self.settings.live_command, current["since"])
                 if entry[0] not in current["baseline"]]
        current["proofs"] = read_proofs(fresh, binding.proof_pattern, binding.error_pattern)
        states = phase_states(binding.phases, current["proofs"], None)
        for item in changes(current["states"], states):
            current["sink"]({"type": "phase", **item})
        current["states"] = states

    def finish(self, outcome: str) -> None:
        current, self.current = self.current, None
        if not current:
            return
        binding: Binding = current["binding"]
        final = phase_states(binding.phases, current["proofs"], outcome)
        for item in changes(current["states"], final):
            current["sink"]({"type": "phase", **item})
        current["sink"]({"type": "finished", "status": outcome, "folder": None, "states": final})

        def close() -> None:
            # packing a recording may take a minute: the next test is recorded meanwhile, into its own folder
            current["screens"].close()
            try:
                current["sink"].flush()
            except OSError:
                pass

        thread = threading.Thread(target=close, daemon=True)
        thread.start()
        self.closing.append(thread)

    def close(self, outcome: str) -> None:
        """The suite run ended: a test still open ends with it; recordings are waited for."""
        self.finish("stopped" if outcome == "stopped" else outcome)
        for thread in self.closing:
            thread.join()


def execute(
    run: dict[str, Any],
    bindings: dict[str, Binding],
    center: Center,
    settings: Settings,
    sink: AftertimeSink,
    simulation: dict[str, Any] | None = None,
) -> None:
    target = run.get("target") or {}
    if run.get("kind") == "center":
        # Suites or cases chosen by hand: no BPMN phases, the log and progress tell the story.
        binding = Binding(id="center", title=target.get("title") or "Запуск", phases=[],
                          cases=list(target.get("cases") or []), run_folder_mask="*", proof_pattern=r"(?!)")
    else:
        binding = bindings.get(run["binding"])
    if binding is None:
        sink({"type": "error", "message": f"Прив'язку «{run['binding']}» не знайдено в налаштуваннях мосту"})
        return
    should_stop = lambda: sink.stop_requested  # noqa: E731
    started = datetime.now()
    suite = run.get("kind") == "center"
    # A suite run records nothing itself: each of its tests gets its own run and recording (CaseRuns).
    # The windows are recorded for simulations too: the phases are timed, the screens are real.
    screens = Screens(settings, sink, run["id"], [] if suite else screens_of(binding))
    screens.start()
    if simulation is not None:
        try:
            sink({"type": "queued", "job": f"simulation-{run['id'][:8]}", "ahead": 0})
            simulate(binding, f"simulation-{run['id'][:8]}", sink, should_stop=should_stop, **simulation)
        finally:
            screens.close()
        return
    cases = CaseRuns(sink.aftertime, run["id"], bindings, settings, sink.echo) if suite else None
    outcome = "error"
    try:
        busy = center.busy()
        job_id = center.run(binding.cases, f"Aftertime · {binding.title}", suites=list(target.get("suites") or []),
                            dry_run=bool(target.get("dryRun")), stop_on_failure=bool(target.get("stopOnFailure")))
        sink({"type": "queued", "job": job_id, "ahead": len(busy["queue"]) + (1 if busy["running"] else 0)})
        result = follow(center, settings, binding, job_id, sink, should_stop=should_stop,
                        on_line=cases.line if cases else None, on_tick=cases.tick if cases else None)
        outcome = (result or {}).get("status") or "error"
    except CenterError as exc:
        sink({"type": "error", "message": f"Центр E2E недоступний: {exc}"})
        return
    finally:
        screens.close()
        if cases:
            cases.close(outcome)
    import_report(settings, binding, run["id"], started, sink, every_launch=run.get("kind") == "center")


def import_report(
    settings: Settings,
    binding: Binding,
    run_id: str,
    since: datetime,
    sink: Callable[[dict], None],
    every_launch: bool = False,
) -> None:
    """Bring the test reports of a finished run into Aftertime reports."""
    if not settings.results_root or not settings.results_root.is_dir():
        return
    if every_launch:
        folders = launches_since(settings.results_root, since)
    else:
        folder = newest_launch_for(settings.results_root, binding.cases, since)
        folders = [folder] if folder else []
    if not folders:
        sink({"type": "log", "line": "Aftertime: звіт прогону в результатах не знайдено"})
        return
    uploader = Uploader(settings.aftertime_url)
    for folder in folders:
        try:
            result = uploader.upload(folder, e2e_run_id=run_id)
            sink({"type": "report", "launch": result["launch_id"], "folder": folder.name})
        except OSError as exc:
            sink({"type": "log", "line": f"Aftertime: звіт {folder.name} не імпортовано: {exc}"})


def push_state(aftertime: Aftertime, center: Center, echo: Callable[[dict], None]) -> None:
    """Mirror what the control center shows (catalog, stand, queue, history) into Aftertime."""
    try:
        state = center.state()
    except CenterError as exc:
        state = {"error": str(exc)}
    state.pop("reports", None)
    try:
        aftertime.push_state(state)
    except OSError as exc:
        echo({"type": "error", "message": f"Aftertime недоступний: {exc}"})


def _keep_pushing_state(aftertime: Aftertime, center: Center, echo: Callable[[dict], None], every: float) -> None:
    # Runs beside the main loop, so Aftertime keeps seeing the stand while a run blocks it.
    while True:
        push_state(aftertime, center, echo)
        time.sleep(every)


def serve(
    aftertime: Aftertime,
    bindings: dict[str, Binding],
    center: Center,
    settings: Settings,
    echo: Callable[[dict], None],
    simulation: dict[str, Any] | None = None,
    once: bool = False,
) -> None:
    echo({"type": "serving", "aftertime": aftertime.url, "center": settings.center_url,
          "simulation": simulation is not None})
    if not once:
        threading.Thread(target=_keep_pushing_state, args=(aftertime, center, echo, settings.poll_seconds * 2),
                         daemon=True).start()
    while True:
        try:
            run = aftertime.claim()
        except OSError as exc:
            echo({"type": "error", "message": f"Aftertime недоступний: {exc}"})
            run = None
        if run:
            echo({"type": "claimed", "run": run["id"], "binding": run["binding"]})
            sink = AftertimeSink(aftertime, run["id"], echo)
            execute(run, bindings, center, settings, sink, simulation)
            try:
                sink.flush()
            except OSError as exc:
                # Aftertime restarting at the end of a run must not stop the bridge: the next runs still come.
                echo({"type": "error", "message": f"Aftertime недоступний: {exc}"})
            if once:
                return
        else:
            time.sleep(settings.poll_seconds)
