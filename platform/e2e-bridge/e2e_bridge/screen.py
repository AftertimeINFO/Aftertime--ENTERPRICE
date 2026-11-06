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
        env: dict[str, str] | None = None,
    ) -> None:
        self.command = command
        self.send = send
        # BRIDGE_RECORDING_ID (and BRIDGE_CASE for a test of a suite) go to every action: a command keeps each
        # recording apart, so the next test can start recording while the previous one is still being packed.
        self.env = dict(env or {})
        self.send_recording = send_recording
        self.started_at = ""
        self.every = every
        self.runner = runner
        self.frames = 0
        self.recorded = False
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._file = Path(tempfile.gettempdir()) / f"aftertime-frame-{os.getpid()}-{id(self)}.jpg"

    def _call(self, action: str, extra: dict[str, str] | None = None, timeout: float = 60) -> bool:
        env = {**self.env, **(extra or {})}
        if not env:
            return self.runner(self.command, action) if timeout == 60 else self.runner(self.command, action, None, timeout=timeout)
        return self.runner(self.command, action, env) if timeout == 60 else self.runner(self.command, action, env, timeout=timeout)

    def start(self) -> None:
        if not self._call("start"):
            return
        # The recording's own clock starts now; frames are offsets from this host time.
        self.started_at = datetime.now(timezone.utc).isoformat()
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def grab(self) -> None:
        captured = datetime.now(timezone.utc).isoformat()
        self._file.unlink(missing_ok=True)
        if self._call("frame", {"BRIDGE_FRAME_OUT": str(self._file)}) and self._file.exists():
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
        self._call("stop")
        self._file.unlink(missing_ok=True)
        if self.send_recording:
            archive = self._file.with_suffix(".zip")
            archive.unlink(missing_ok=True)
            started = archive.with_name(archive.name + ".started")
            started.unlink(missing_ok=True)
            if self._call("pack", {"BRIDGE_RECORDING_OUT": str(archive)}, timeout=600) and archive.exists():
                # a command that captured from before ``start`` (the test itself buffers its first frames)
                # says when its recording really began: <archive>.started, an ISO time
                try:
                    began = started.read_text(encoding="utf-8").strip() if started.exists() else ""
                    datetime.fromisoformat(began)
                except ValueError:
                    began = ""
                try:
                    self.send_recording(archive, began or self.started_at)
                    self.recorded = True
                except OSError:
                    pass
                finally:
                    archive.unlink(missing_ok=True)
                    started.unlink(missing_ok=True)
