import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

from e2e_bridge.center import Center
from e2e_bridge.phases import Proof, changes, phase_states, read_proofs
from e2e_bridge.runner import Binding, Settings, find_run_folder, folder_states, follow

PHASES = ["order", "desks", "issue", "deposit", "pay", "ride", "return", "noextra", "depositreturn"]
BINDING = Binding(
    id="rental",
    title="Видача",
    phases=PHASES,
    cases=["case-a"],
    run_folder_mask="suite-*",
    proof_pattern=r"verify-(?P<run>\d+)-(?P<phase>[a-z]+)\.json$",
    error_pattern=r"verify-(?P<run>\d+)-(?P<phase>[a-z]+)-error\.txt$",
)


def states(result):
    return {item["phase"]: item["state"] for item in result}


def proofs(*phases, failed=()):
    found = {phase: Proof(phase, True, f"verify-1-{phase}.json") for phase in phases}
    found.update({phase: Proof(phase, False, f"verify-1-{phase}-error.txt") for phase in failed})
    return found


def test_running_case_marks_the_phase_after_the_last_proof_active():
    result = states(phase_states(PHASES, proofs("order", "issue")))
    assert result["order"] == result["desks"] == result["issue"] == "passed"
    assert result["deposit"] == "active"
    assert result["pay"] == "pending"


def test_unproven_phases_before_a_proof_are_inferred():
    result = phase_states(PHASES, proofs("order", "issue"))
    desks = next(item for item in result if item["phase"] == "desks")
    assert desks == {"phase": "desks", "inferred": True, "state": "passed"}


def test_passed_case_closes_phases_without_proofs():
    result = states(phase_states(PHASES, proofs("order", "issue", "deposit", "pay", "return", "noextra"), "passed"))
    assert set(result.values()) == {"passed"}


def test_failed_proof_stops_the_chain():
    result = states(phase_states(PHASES, proofs("order", "issue", failed=("deposit",)), None))
    assert result["deposit"] == "failed"
    assert result["pay"] == result["depositreturn"] == "not-reached"


def test_failed_case_without_proof_fails_the_next_phase():
    result = states(phase_states(PHASES, proofs("order"), "failed"))
    assert result["order"] == "passed"
    assert result["desks"] == "failed"
    assert result["issue"] == "not-reached"
    assert states(phase_states(PHASES, {}, "stopped"))["order"] == "stopped"


def test_proof_files_are_read_with_verified_flag_and_errors():
    found = read_proofs(
        [
            ("case-a-verify-7-order.json", json.dumps({"phase": "order", "verified": True})),
            ("case-a-verify-7-issue.json", json.dumps({"phase": "issue", "verified": False, "defect": "рядків 6 з 7"})),
            ("case-a-verify-7-pay-error.txt", "ПРРО не відповів"),
            ("case-a-verify-7-pay.json", json.dumps({"verified": True})),
            ("summary.json", "{}"),
        ],
        BINDING.proof_pattern,
        BINDING.error_pattern,
    )
    assert found["order"].passed is True
    assert found["issue"] == Proof("issue", False, "case-a-verify-7-issue.json", "рядків 6 з 7")
    assert found["pay"].passed is False
    assert "summary" not in found


def test_changes_reports_only_moved_phases():
    before = phase_states(PHASES, proofs("order"))
    after = phase_states(PHASES, proofs("order", "issue"))
    assert [(item["phase"], item["state"]) for item in changes(before, after)] == [
        ("desks", "passed"), ("issue", "passed"), ("deposit", "active"),
    ]


def make_run_folder(root: Path, name="suite-20261103-120000", failed_at=None) -> Path:
    folder = root / name
    folder.mkdir()
    for phase in ["order", "issue", "deposit", "pay", "return", "noextra", "depositreturn"]:
        if phase == failed_at:
            (folder / f"case-a-verify-9-{phase}-error.txt").write_text("збій", encoding="utf-8")
            break
        (folder / f"case-a-verify-9-{phase}.json").write_text(json.dumps({"verified": True}), encoding="utf-8")
    return folder


def test_run_folder_is_found_in_the_job_log(tmp_path):
    folder = make_run_folder(tmp_path)
    lines = [
        "== suite virtual-hw: run-vm-suite.ps1",
        f"Результат: passed  (докази: {folder})",
        f"{tmp_path}\\other-20261103",
    ]
    assert find_run_folder(lines, tmp_path, "suite-*") == folder
    assert find_run_folder(["нічого"], tmp_path, "suite-*") is None


def test_folder_states_from_a_recorded_failure(tmp_path):
    folder = make_run_folder(tmp_path, failed_at="pay")
    result = states(folder_states(BINDING, folder, "failed"))
    assert result["deposit"] == "passed"
    assert result["pay"] == "failed"
    assert result["ride"] == "not-reached"


class FakeCenter:
    def __init__(self, script):
        self.script = list(script)
        self.calls = 0

    def job(self, job_id, start=0):
        self.calls += 1
        return self.script.pop(0) if len(self.script) > 1 else self.script[0]


def test_follow_streams_log_and_final_phases(tmp_path):
    folder = make_run_folder(tmp_path)
    center = FakeCenter([
        {"status": "queued", "lines": [], "next": 0},
        {"status": "running", "lines": ["== suite virtual-hw"], "next": 1},
        {"status": "passed", "lines": [f"Результат: passed  (докази: {folder})", "--- finished: passed (exit 0)"], "next": 3},
    ])
    events = []
    settings = Settings(center_url="", run_root=tmp_path, poll_seconds=0)
    result = follow(center, settings, BINDING, "job-1", events.append, sleep=lambda _: None)
    assert result["status"] == "passed"
    assert result["folder"] == str(folder)
    assert set(states(result["states"]).values()) == {"passed"}
    assert [event["line"] for event in events if event["type"] == "log"][-1] == "--- finished: passed (exit 0)"
    assert [event["status"] for event in events if event["type"] == "status"] == ["queued", "running"]


def test_follow_uses_live_proofs_while_running(tmp_path, monkeypatch):
    calls = []

    def live(command, since):
        calls.append(command)
        old = [("verify-4-order.json", "{}"), ("verify-4-issue.json", "{}"), ("verify-4-deposit.json", "{}")]
        return old if len(calls) == 1 else old + [("verify-5-order.json", "{}"), ("verify-5-issue.json", "{}")]

    monkeypatch.setattr("e2e_bridge.runner.live_entries", live)
    center = FakeCenter([
        {"status": "running", "lines": [], "next": 0},
        {"status": "failed", "lines": ["--- finished: failed (exit 1)"], "next": 1},
    ])
    events = []
    binding = Binding(**{**BINDING.__dict__, "live": True})
    settings = Settings(center_url="", run_root=tmp_path, live_command="probe", poll_seconds=0)
    result = follow(center, settings, binding, "job-2", events.append, sleep=lambda _: None)
    phase_events = [(event["phase"], event["state"]) for event in events if event["type"] == "phase"]
    # The first call is the baseline: proofs of an earlier run (verify-4-*) never count.
    assert calls == ["probe", "probe"]
    assert ("issue", "passed") in phase_events and ("deposit", "active") in phase_events
    assert ("deposit", "passed") not in phase_events
    assert states(result["states"])["deposit"] == "failed"
    assert result["folder"] is None


@pytest.fixture
def center_server():
    received = []

    class Handler(BaseHTTPRequestHandler):
        def _reply(self, payload):
            body = json.dumps(payload).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            received.append(("GET", self.path, None))
            if self.path == "/api/state":
                self._reply({"running": "j0", "queue": ["j9"], "jobs": [], "history": []})
            else:
                self._reply({"status": "running", "lines": ["x"], "next": 1})

        def do_POST(self):
            length = int(self.headers.get("Content-Length") or 0)
            body = json.loads(self.rfile.read(length) or b"null")
            received.append(("POST", self.path, body))
            self._reply({"id": "j1"} if self.path == "/api/run" else {"ok": True})

        def log_message(self, *args):
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_port}", received
    server.shutdown()


def test_center_client_speaks_the_control_center_api(center_server):
    url, received = center_server
    center = Center(url)
    assert center.busy() == {"running": "j0", "queue": ["j9"]}
    assert center.run(["case-a"], "Aftertime · Видача") == "j1"
    assert center.job("j1", 4)["next"] == 1
    assert center.stop("j1") is True
    assert ("POST", "/api/run", {"cases": ["case-a"], "title": "Aftertime · Видача"}) in received
    assert ("GET", "/api/job/j1?from=4", None) in received


def test_bindings_come_from_packs_with_stand_overrides(tmp_path):
    from e2e_bridge.runner import load_bindings

    pack = tmp_path / "Aftertime-model-rental"
    (pack / "e2e").mkdir(parents=True)
    (pack / "aftertime-pack.json").write_text(json.dumps({"id": "rental", "bindings": "e2e/bindings.json"}), encoding="utf-8")
    (pack / "e2e" / "bindings.json").write_text(json.dumps(
        {"rental-issue": {"title": "Видача", "phases": ["order", "issue"], "runner": {"cases": ["case-a"]}}}), encoding="utf-8")
    config = tmp_path / "Aftertime-config"
    (config / "e2e").mkdir(parents=True)
    (config / "e2e" / "bindings.json").write_text(json.dumps(
        {"rental-issue": {"runner": {"proofPattern": "verify-(?P<phase>[a-z]+)\\.json$", "live": True}}}), encoding="utf-8")
    binding = load_bindings(config, tmp_path)["rental-issue"]
    assert binding.phases == ["order", "issue"]
    assert binding.cases == ["case-a"]
    assert binding.live is True
    assert binding.proof_pattern == r"verify-(?P<phase>[a-z]+)\.json$"


def test_failed_run_keeps_live_proofs_when_the_folder_has_none(tmp_path, monkeypatch):
    folder = tmp_path / "suite-20261103-124201"
    folder.mkdir()
    (folder / "case-a-FAILURE.txt").write_text("the case did not pass", encoding="utf-8")
    live = [(f"verify-5-{phase}.json", "{}") for phase in ["order", "issue", "deposit", "pay", "return", "noextra"]]
    calls = []
    monkeypatch.setattr("e2e_bridge.runner.live_entries", lambda command, since: calls.append(1) or ([] if len(calls) == 1 else live))
    center = FakeCenter([
        {"status": "running", "lines": [], "next": 0},
        {"status": "failed", "lines": [f"Результат: failed  (докази: {folder})"], "next": 1},
    ])
    binding = Binding(**{**BINDING.__dict__, "live": True})
    settings = Settings(center_url="", run_root=tmp_path, live_command="probe", poll_seconds=0)
    result = follow(center, settings, binding, "job-3", lambda event: None, sleep=lambda _: None)
    assert result["folder"] == str(folder)
    final = states(result["states"])
    assert final["noextra"] == "passed"
    assert final["depositreturn"] == "failed"
    assert final["order"] == "passed"
