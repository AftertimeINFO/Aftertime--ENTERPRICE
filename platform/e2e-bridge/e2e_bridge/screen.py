"""Screen of the machine under test during a run, sent to Aftertime frame by frame.

``SCREEN_COMMAND`` in ``bridge.env`` is a command that accepts ``-Action start|frame|stop|pack``;
``frame`` writes the latest JPEG to ``BRIDGE_FRAME_OUT``; ``pack`` writes the whole recording — a zip
of JPEG frames named by milliseconds since the recording started — to ``BRIDGE_RECORDING_OUT``.
How the screen is captured (inside a VM session, from a hypervisor, …) is the command's business.
Aftertime packs the recording into a video in the background.
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
SendRecording = Callable[[Path, str], None]


def _run(command: str, action: str, env: dict[str, str] | None = None, timeout: float = 60) -> bool:
    try:
        # No pipes: a recorder the command leaves running would hold them open and block the bridge.
        result = subprocess.run(f"{command} -Action {action}", shell=True, stdin=subprocess.DEVNULL,
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                timeout=timeout, env={**os.environ, **(env or {})})
        return result.returncode == 0
    except subprocess.SubprocessError:
        return False


class ScreenRecorder:
    """Start capture, pull a frame every ``every`` seconds, stop on ``close``."""

    def __init__(
        self,
        command: str,
        send: Send,
        every: float = 3,
        runner: Callable[..., bool] = _run,
        send_recording: SendRecording | None = None,
    ) -> None:
        self.command = command
        self.send = send
        self.send_recording = send_recording
        self.started_at = ""
        self.every = every
        self.runner = runner
        self.frames = 0
        self.recorded = False
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._file = Path(tempfile.gettempdir()) / f"aftertime-frame-{os.getpid()}-{id(self)}.jpg"

    def start(self) -> None:
        if not self.runner(self.command, "start"):
            return
        # The recording's own clock starts now; frames are offsets from this host time.
        self.started_at = datetime.now(timezone.utc).isoformat()
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
        if self.send_recording:
            archive = self._file.with_suffix(".zip")
            archive.unlink(missing_ok=True)
            if self.runner(self.command, "pack", {"BRIDGE_RECORDING_OUT": str(archive)}, timeout=600) and archive.exists():
                try:
                    self.send_recording(archive, self.started_at)
                    self.recorded = True
                except OSError:
                    pass
                finally:
                    archive.unlink(missing_ok=True)
