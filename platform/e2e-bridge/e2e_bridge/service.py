"""Serve Aftertime: claim requested runs, execute them, report progress back."""

from __future__ import annotations

import json
import time
import urllib.request
from typing import Any, Callable

from e2e_bridge.center import Center, CenterError
from e2e_bridge.phases import PASSED, Proof, changes, phase_states
from e2e_bridge.runner import Binding, Settings, follow


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

    def report(self, run_id: str, events: list[dict[str, Any]]) -> dict[str, Any]:
        return self._call("POST", f"/lab/e2e-runs/{run_id}/events", {"events": events})


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


def execute(
    run: dict[str, Any],
    bindings: dict[str, Binding],
    center: Center,
    settings: Settings,
    sink: AftertimeSink,
    simulation: dict[str, Any] | None = None,
) -> None:
    binding = bindings.get(run["binding"])
    if binding is None:
        sink({"type": "error", "message": f"Прив'язку «{run['binding']}» не знайдено в налаштуваннях мосту"})
        return
    should_stop = lambda: sink.stop_requested  # noqa: E731
    if simulation is not None:
        sink({"type": "queued", "job": f"simulation-{run['id'][:8]}", "ahead": 0})
        simulate(binding, f"simulation-{run['id'][:8]}", sink, should_stop=should_stop, **simulation)
        return
    try:
        busy = center.busy()
        job_id = center.run(binding.cases, f"Aftertime · {binding.title}")
        sink({"type": "queued", "job": job_id, "ahead": len(busy["queue"]) + (1 if busy["running"] else 0)})
        follow(center, settings, binding, job_id, sink, should_stop=should_stop)
    except CenterError as exc:
        sink({"type": "error", "message": f"Центр E2E недоступний: {exc}"})


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
            sink.flush()
            if once:
                return
        else:
            time.sleep(settings.poll_seconds)
