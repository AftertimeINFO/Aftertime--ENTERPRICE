"""Screen of the machine under test during a run, sent to Aftertime frame by frame.

``SCREEN_COMMAND`` in ``bridge.env`` is a command that accepts ``-Action start|frame|stop``;
``frame`` writes the latest JPEG to the path in ``BRIDGE_FRAME_OUT``.  How the screen is
captured (inside a VM session, from a hypervisor, …) is the command's business.
"""

from __future__ import annotations

import os
import subprocess
import tempfile
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

Send = Callable[[bytes, str], None]


def _run(command: str, action: str, env: dict[str, str] | None = None, timeout: float = 60) -> bool:
    try:
        result = subprocess.run(f"{command} -Action {action}", shell=True, capture_output=True,
                                timeout=timeout, env={**os.environ, **(env or {})})
        return result.returncode == 0
    except subprocess.SubprocessError:
        return False


class ScreenRecorder:
    """Start capture, pull a frame every ``every`` seconds, stop on ``close``."""

    def __init__(self, command: str, send: Send, every: float = 3, runner: Callable[..., bool] = _run) -> None:
        self.command = command
        self.send = send
        self.every = every
        self.runner = runner
        self.frames = 0
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._file = Path(tempfile.gettempdir()) / f"aftertime-frame-{os.getpid()}-{id(self)}.jpg"

    def start(self) -> None:
        if not self.runner(self.command, "start"):
            return
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def grab(self) -> None:
        captured = datetime.now(timezone.utc).isoformat()
        self._file.unlink(missing_ok=True)
        if self.runner(self.command, "frame", {"BRIDGE_FRAME_OUT": str(self._file)}) and self._file.exists():
            try:
                self.send(self._file.read_bytes(), captured)
                self.frames += 1
            except OSError:
                pass  # Aftertime briefly unavailable: the next frame will do

    def _loop(self) -> None:
        while not self._stop.wait(self.every):
            self.grab()

    def close(self) -> None:
        """Last frame (the screen as the run ended) and stop capturing."""
        if self._thread is None:
            return
        self._stop.set()
        self._thread.join(timeout=self.every + 60)
        self.grab()
        self.runner(self.command, "stop")
        self._file.unlink(missing_ok=True)
