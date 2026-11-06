from pathlib import Path

from e2e_bridge.runner import Binding, Screen, Settings
from e2e_bridge.service import CaseRuns, Screens, screens_of


class FakeAftertime:
    def __init__(self):
        self.cases = []
        self.events = {}

    def start_case(self, run_id, case, binding):
        child = f"child-{len(self.cases) + 1}"
        self.cases.append((run_id, case, binding, child))
        return {"id": child}

    def report(self, run_id, events):
        self.events.setdefault(run_id, []).extend(events)
        return {}


def binding(case, phases=("order", "issue"), screens=None):
    return Binding(id=case, title=case, phases=list(phases), cases=[case], run_folder_mask="*",
                   proof_pattern=r"-(?P<phase>[a-z]+)\.json$", extra={"screens": screens} if screens is not None else {})


def test_every_test_of_a_suite_run_gets_its_own_run_between_its_marks():
    aftertime = FakeAftertime()
    bindings = {"a": binding("a"), "b": binding("b"), "suite": Binding("suite", "s", [], ["a", "b"], "*", "x")}
    runs = CaseRuns(aftertime, "parent", bindings, Settings(center_url="", run_root=Path(".")))
    for line in ("== suite first:", "  >> CASE|start|a", "  >> PREP|ok|1|2|clean|10||Clean", "  >> CASE|end|a|passed",
                 "between the tests", "CASE|start|b", "b is running", "CASE|start|unknown", "CASE|end|unknown|passed"):
        runs.line(line)
    runs.close("failed")
    # the binding made for a test alone wins over a suite binding that lists it too
    assert [(parent, case, bound) for parent, case, bound, _ in aftertime.cases] == [("parent", "a", "a"), ("parent", "b", "b")]
    first, second = aftertime.events["child-1"], aftertime.events["child-2"]
    assert [event["line"] for event in first if event["type"] == "log"] == [
        "  >> CASE|start|a", "  >> PREP|ok|1|2|clean|10||Clean", "  >> CASE|end|a|passed"]
    assert [event["status"] for event in first if event["type"] == "finished"] == ["passed"]
    # b never said it ended: a new test begins (unknown has no binding) and b is closed as stopped
    assert [event["status"] for event in second if event["type"] == "finished"] == ["stopped"]
    assert "between the tests" not in [event.get("line") for event in first + second]


def test_a_test_records_only_the_windows_set_for_it():
    settings = Settings(center_url="", run_root=Path("."), screens=[
        Screen("screen", "VM", "vm"), Screen("client", "Client", "client"), Screen("equipment", "Equipment", "eq")])
    sink = type("Sink", (), {"aftertime": FakeAftertime()})()
    assert [screen.id for screen, _ in Screens(settings, sink, "r", screens_of(binding("a", screens=["client"]))).recorders] == ["client"]
    assert len(Screens(settings, sink, "r", screens_of(binding("a"))).recorders) == 3
    assert Screens(settings, sink, "r", []).recorders == []
    recorder = Screens(settings, sink, "r1", None, "case-x").recorders[0][1]
    assert recorder.env == {"BRIDGE_RECORDING_ID": "r1", "BRIDGE_CASE": "case-x"}


def test_bridge_settings_name_the_settings_folder_instead_of_a_fixed_path(tmp_path, monkeypatch):
    (tmp_path / "e2e").mkdir()
    (tmp_path / "e2e" / "screens.json").write_text('[{"id": "web", "title": "Web", "command": "w"}]', encoding="utf-8")
    (tmp_path / "e2e" / "bridge.env").write_text(
        "SCREENS_FILE=%AFTERTIME_CONFIG_DIR%\e2e\screens.json\nPACKS_DIR=${AFTERTIME_CONFIG_DIR}\nLIVE_COMMAND=run %AFTERTIME_CONFIG_DIR%\live.ps1\n",
        encoding="utf-8")
    monkeypatch.delenv("AFTERTIME_CONFIG_DIR", raising=False)
    settings = Settings.load(tmp_path)
    assert [screen.id for screen in settings.screens] == ["web"]
    assert settings.packs_dir == tmp_path.resolve() and settings.live_command == f"run {tmp_path.resolve()}\live.ps1"
