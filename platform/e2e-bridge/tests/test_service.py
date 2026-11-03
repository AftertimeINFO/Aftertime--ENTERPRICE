from e2e_bridge.runner import Binding, Settings
from e2e_bridge.service import AftertimeSink, execute, simulate

BINDING = Binding(id="rental", title="Видача", phases=["order", "issue", "return"], cases=["case-a"],
                  run_folder_mask="suite-*", proof_pattern=r"-(?P<phase>[a-z]+)\.json$")


class FakeAftertime:
    def __init__(self, stop_after=None):
        self.reports = []
        self.stop_after = stop_after

    def report(self, run_id, events):
        self.reports.append(events)
        return {"stop_requested": self.stop_after is not None and len(self.reports) >= self.stop_after}


def flat(aftertime):
    return [event for batch in aftertime.reports for event in batch]


def test_sink_batches_log_lines_and_sends_other_events_at_once():
    aftertime = FakeAftertime()
    sink = AftertimeSink(aftertime, "r1")
    for index in range(3):
        sink({"type": "log", "line": str(index)})
    assert aftertime.reports == []
    sink({"type": "phase", "phase": "order", "state": "passed"})
    assert [event["type"] for event in aftertime.reports[0]] == ["log", "log", "log", "phase"]


def test_simulation_walks_every_phase_to_a_passed_case():
    events = []
    result = simulate(BINDING, "sim", events.append, sleep=lambda _: None)
    assert result["status"] == "passed"
    assert [item["state"] for item in result["states"]] == ["passed"] * 3
    assert ("issue", "active") in [(event["phase"], event["state"]) for event in events if event["type"] == "phase"]


def test_simulation_can_fail_at_a_phase():
    result = simulate(BINDING, "sim", lambda _: None, fail_at="issue", sleep=lambda _: None)
    assert result["status"] == "failed"
    assert [item["state"] for item in result["states"]] == ["passed", "failed", "not-reached"]


def test_execute_honours_a_stop_requested_in_aftertime():
    aftertime = FakeAftertime(stop_after=2)
    sink = AftertimeSink(aftertime, "r2")
    execute({"id": "r2-uuid", "binding": "rental"}, {"rental": BINDING}, None, Settings("", "."), sink,
            {"step_seconds": 0, "fail_at": None, "sleep": lambda _: None})
    finished = [event for event in flat(aftertime) if event["type"] == "finished"][0]
    assert finished["status"] == "stopped"


def test_execute_reports_an_unknown_binding():
    aftertime = FakeAftertime()
    sink = AftertimeSink(aftertime, "r3")
    execute({"id": "r3", "binding": "missing"}, {}, None, Settings("", "."), sink)
    assert flat(aftertime)[0]["type"] == "error"


def test_center_run_starts_chosen_suites_and_imports_every_new_launch(tmp_path, monkeypatch):
    calls = {}

    class Center:
        def busy(self):
            return {"running": None, "queue": []}

        def run(self, cases, title, suites=None, dry_run=False, stop_on_failure=False):
            calls["run"] = (cases, suites, dry_run, stop_on_failure)
            return "job-9"

    def fake_follow(center, settings, binding, job_id, sink, should_stop):
        assert binding.phases == [] and job_id == "job-9"
        sink({"type": "finished", "status": "passed", "states": []})

    imported = []
    monkeypatch.setattr("e2e_bridge.service.follow", fake_follow)
    monkeypatch.setattr("e2e_bridge.service.import_report",
                        lambda settings, binding, run_id, since, sink, every_launch=False: imported.append(every_launch))
    aftertime = FakeAftertime()
    run = {"id": "r4", "binding": "", "kind": "center",
           "target": {"suites": ["chains"], "cases": [], "title": "chains", "dryRun": True, "stopOnFailure": True}}
    execute(run, {}, Center(), Settings("", tmp_path), AftertimeSink(aftertime, "r4"))
    assert calls["run"] == ([], ["chains"], True, True)
    assert imported == [True]
    assert [event["type"] for event in flat(aftertime)] == ["queued", "finished"]


def test_center_refuses_a_run_without_suites_or_cases():
    import pytest

    from e2e_bridge.center import Center, CenterError

    with pytest.raises(CenterError):
        Center("http://127.0.0.1:9").run([], "порожньо")
