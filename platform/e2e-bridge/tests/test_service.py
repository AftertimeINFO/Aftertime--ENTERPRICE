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
