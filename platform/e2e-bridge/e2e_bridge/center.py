"""Client of an E2E control center that queues runs and streams their logs.

Expected API (one stand, one run at a time):
``GET /api/state`` → ``{running, queue, jobs, history}``;
``POST /api/run`` ``{"cases": [...], "title": ...}`` → ``{id}``;
``GET /api/job/<id>?from=N`` → ``{status, lines, next, ...}``;
``POST /api/stop/<id>`` → ``{ok}``.
"""

from __future__ import annotations

import json
import urllib.request
from typing import Any

FINISHED = {"passed", "failed", "stopped"}


class CenterError(RuntimeError):
    pass


class Center:
    def __init__(self, url: str, timeout: float = 30) -> None:
        self.url = url.rstrip("/")
        self.timeout = timeout

    def _call(self, method: str, path: str, body: dict | None = None) -> Any:
        request = urllib.request.Request(
            f"{self.url}{path}",
            method=method,
            data=json.dumps(body).encode("utf-8") if body is not None else None,
            headers={"Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                return json.load(response)
        except OSError as exc:
            raise CenterError(f"{method} {self.url}{path}: {exc}") from exc

    def state(self) -> dict[str, Any]:
        return self._call("GET", "/api/state")

    def busy(self) -> dict[str, Any]:
        state = self.state()
        return {"running": state.get("running"), "queue": list(state.get("queue") or [])}

    def run(self, cases: list[str], title: str) -> str:
        return self._call("POST", "/api/run", {"cases": cases, "title": title})["id"]

    def job(self, job_id: str, start: int = 0) -> dict[str, Any]:
        return self._call("GET", f"/api/job/{job_id}?from={start}")

    def stop(self, job_id: str) -> bool:
        return bool(self._call("POST", f"/api/stop/{job_id}").get("ok"))
