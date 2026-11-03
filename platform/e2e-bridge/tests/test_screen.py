from pathlib import Path

from e2e_bridge.screen import ScreenRecorder


def test_recorder_starts_sends_frames_and_stops_with_a_last_frame():
    calls, sent = [], []

    def runner(command, action, env=None, timeout=60):
        calls.append(action)
        if action == "frame":
            Path(env["BRIDGE_FRAME_OUT"]).write_bytes(b"\xff\xd8frame")
        return True

    recorder = ScreenRecorder("screen", lambda jpeg, at: sent.append((jpeg, at)), every=0.01, runner=runner)
    recorder.start()
    while recorder.frames < 2:
        pass
    recorder.close()
    assert calls[0] == "start" and calls[-1] == "stop"
    assert calls.count("frame") >= 3  # the loop plus the last frame on close
    assert sent[0][0] == b"\xff\xd8frame" and sent[0][1].endswith("+00:00")


def test_recorder_does_nothing_when_capture_cannot_start():
    calls = []
    recorder = ScreenRecorder("screen", lambda jpeg, at: None, runner=lambda command, action, env=None: calls.append(action) or False)
    recorder.start()
    recorder.close()
    assert calls == ["start"]
