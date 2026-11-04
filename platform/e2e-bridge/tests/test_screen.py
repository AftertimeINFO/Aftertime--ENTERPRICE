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


def test_recording_is_packed_and_sent_after_the_run():
    recordings = []

    def runner(command, action, env=None, timeout=60):
        if action == "frame":
            Path(env["BRIDGE_FRAME_OUT"]).write_bytes(b"\xff\xd8frame")
        if action == "pack":
            Path(env["BRIDGE_RECORDING_OUT"]).write_bytes(b"PK zip")
        return True

    recorder = ScreenRecorder("screen", lambda jpeg, at: None, every=0.01, runner=runner,
                              send_recording=lambda path, started: recordings.append((path.read_bytes(), started)))
    recorder.start()
    recorder.close()
    assert recordings and recordings[0][0] == b"PK zip"
    assert recordings[0][1] == recorder.started_at and recorder.recorded


def test_screens_are_the_main_window_and_the_listed_ones(tmp_path):
    import json

    from e2e_bridge.runner import load_screens

    listed = tmp_path / "screens.json"
    listed.write_text(json.dumps([{"id": "web", "title": "Веб-застосунок", "command": "web", "seconds": 2},
                                  {"id": "screen", "command": "ignored"}, {"title": "без id"}]), encoding="utf-8")
    screens = load_screens({"SCREEN_COMMAND": "vm", "SCREEN_SECONDS": "3", "SCREENS_FILE": str(listed)})
    assert [(item.id, item.command, item.seconds) for item in screens] == [("screen", "vm", 3.0), ("web", "web", 2.0)]
    assert load_screens({}) == []
