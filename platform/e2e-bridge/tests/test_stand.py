"""The stand under test (configuration version) is read by STAND_COMMAND and kept with every run."""

import sys

from e2e_bridge.runner import Settings, read_stand
from e2e_bridge.service import report_stand


def command(code: str) -> str:
    return f'"{sys.executable}" -c "{code}"'


def test_read_stand_takes_version_and_source():
    stand = read_stand(command("print('{\\\"version\\\": \\\"1.75.0.2\\\", \\\"source\\\": \\\"http://x/test/version\\\"}')"))
    assert stand == {"version": "1.75.0.2", "source": "http://x/test/version"}


def test_read_stand_without_command_is_none():
    assert read_stand("") is None


def test_read_stand_keeps_a_failure_as_error():
    assert "error" in read_stand(command("print('not json')"))
    assert "error" in read_stand(command("import sys; sys.stderr.write('boom')"))
    assert read_stand(command("print('{\\\"error\\\": \\\"down\\\"}')")) == {"error": "down"}


def test_report_stand_sends_event_and_log_line(tmp_path):
    settings = Settings(center_url="", run_root=tmp_path,
                        stand_command=command("print('{\\\"version\\\": \\\"1.75.0.2\\\"}')"))
    events = []
    assert report_stand(settings, events.append, "start") == {"version": "1.75.0.2"}
    assert events[0] == {"type": "stand", "version": "1.75.0.2"}
    assert "1.75.0.2" in events[1]["line"] and "початок" in events[1]["line"]


def test_report_stand_is_silent_without_command(tmp_path):
    events = []
    assert report_stand(Settings(center_url="", run_root=tmp_path), events.append, "end") is None
    assert events == []


def test_settings_read_stand_command(tmp_path, monkeypatch):
    (tmp_path / "e2e").mkdir()
    (tmp_path / "e2e" / "bridge.env").write_text("STAND_COMMAND=pwsh -File %AFTERTIME_CONFIG_DIR%\\v.ps1\n", encoding="utf-8")
    monkeypatch.delenv("STAND_COMMAND", raising=False)
    monkeypatch.setenv("AFTERTIME_CONFIG_DIR", str(tmp_path))
    assert Settings.load(tmp_path).stand_command == f"pwsh -File {tmp_path}\\v.ps1"
