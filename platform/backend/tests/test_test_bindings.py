import json

from app.services.bpmn_profile import parse_bpmn
from app.services.test_bindings import load_bindings, phase_coverage, read_test_annotations

BINDINGS = {"order-flow": {"title": "Замовлення", "phases": ["order", "wait", "issue"], "runner": {"cases": ["x"]}}}


def process(binding: str | None = "order-flow", phases: dict[str, str] | None = None) -> str:
    phases = {"order": "order", "wait": "wait", "issue": "issue"} if phases is None else phases
    attr = lambda element: f' aftertime:testPhase="{phases[element]}"' if element in phases else ""  # noqa: E731
    bound = f' aftertime:testBinding="{binding}"' if binding else ""
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<bpmn:definitions xmlns:bpmn="http://www.omg.org/spec/BPMN/20100524/MODEL" xmlns:aftertime="https://aftertime.local/bpmn" id="d">
  <bpmn:process id="p"{bound}>
    <bpmn:startEvent id="start" />
    <bpmn:userTask id="order" name="Оформити"{attr("order")} />
    <bpmn:intermediateCatchEvent id="wait" name="Очікування"{attr("wait")}><bpmn:timerEventDefinition /></bpmn:intermediateCatchEvent>
    <bpmn:serviceTask id="issue" name="Видати"{attr("issue")} />
    <bpmn:exclusiveGateway id="gate"{attr("gate")} />
    <bpmn:endEvent id="end" />
    <bpmn:sequenceFlow id="f1" sourceRef="start" targetRef="order" />
    <bpmn:sequenceFlow id="f2" sourceRef="order" targetRef="wait" />
    <bpmn:sequenceFlow id="f3" sourceRef="wait" targetRef="issue" />
    <bpmn:sequenceFlow id="f4" sourceRef="issue" targetRef="gate" />
    <bpmn:sequenceFlow id="f5" sourceRef="gate" targetRef="end" />
  </bpmn:process>
</bpmn:definitions>"""


def test_phase_makes_the_element_wait_for_its_test_event():
    nodes = {node["node_key"]: node["config"] for node in parse_bpmn(process()).nodes}
    assert nodes["order"]["test_phase"] == "order"
    assert nodes["order"]["capability"] == "test.phase.order"
    assert nodes["wait"]["capability"] == "test.phase.wait"
    assert "capability" not in nodes["gate"]


def test_fully_bound_process_is_covered():
    coverage = phase_coverage(process(), BINDINGS)
    assert coverage["covered"] is True
    assert [(item["phase"], item["element"]) for item in coverage["phases"]] == [
        ("order", "order"), ("wait", "wait"), ("issue", "issue"),
    ]
    assert coverage["warnings"] == []


def test_coverage_names_missing_unknown_duplicated_and_misplaced_phases():
    coverage = phase_coverage(
        process(phases={"order": "order", "wait": "order", "issue": "extra", "gate": "issue"}), BINDINGS
    )
    text = " ".join(coverage["errors"])
    assert coverage["covered"] is False
    assert "без елемента BPMN: wait" in text
    assert "немає в тесті: extra" in text
    assert "кількох елементах: order" in text
    assert "проміжній події очікування: gate" in text


def test_unbound_process_has_no_coverage_and_unknown_binding_is_an_error():
    assert phase_coverage(process(binding=None), BINDINGS) is None
    assert "не знайдено" in phase_coverage(process(binding="missing"), BINDINGS)["errors"][0]


def test_activities_without_a_phase_are_reported_as_untested():
    annotations = read_test_annotations(process(phases={"order": "order"}))
    assert annotations["untested"] == ["issue"]


def test_bindings_are_read_from_the_settings_directory(tmp_path, monkeypatch):
    (tmp_path / "e2e").mkdir()
    (tmp_path / "e2e" / "bindings.json").write_text(json.dumps(BINDINGS), encoding="utf-8")
    monkeypatch.setenv("AFTERTIME_CONFIG_DIR", str(tmp_path))
    monkeypatch.setenv("AFTERTIME_PACKS_DIR", str(tmp_path / "no-packs"))
    assert load_bindings() == BINDINGS
    monkeypatch.setenv("AFTERTIME_CONFIG_DIR", str(tmp_path / "absent"))
    assert load_bindings() == {}


# --- tests that follow a path through a shared process ---------------------------------------

from app.services.process_paths import walk_path, parse_graph  # noqa: E402
from app.services.test_bindings import process_bindings, workflow_coverage  # noqa: E402

SHARED = """<?xml version="1.0" encoding="UTF-8"?>
<bpmn:definitions xmlns:bpmn="http://www.omg.org/spec/BPMN/20100524/MODEL" xmlns:aftertime="https://aftertime.local/bpmn" id="d">
  <bpmn:process id="issue">
    <bpmn:startEvent id="start" />
    <bpmn:userTask id="order" aftertime:testPhase="order" />
    <bpmn:serviceTask id="deposit" aftertime:testPhase="deposit" />
    <bpmn:exclusiveGateway id="extra" />
    <bpmn:serviceTask id="charge" aftertime:testPhase="charge" />
    <bpmn:userTask id="close" aftertime:testPhase="close" />
    <bpmn:endEvent id="end" />
    <bpmn:sequenceFlow id="f1" sourceRef="start" targetRef="order" />
    <bpmn:sequenceFlow id="f2" sourceRef="order" targetRef="deposit" />
    <bpmn:sequenceFlow id="f3" sourceRef="deposit" targetRef="extra" />
    <bpmn:sequenceFlow id="f_extra" sourceRef="extra" targetRef="charge" />
    <bpmn:sequenceFlow id="f_none" sourceRef="extra" targetRef="close" />
    <bpmn:sequenceFlow id="f6" sourceRef="charge" targetRef="close" />
    <bpmn:sequenceFlow id="f7" sourceRef="close" targetRef="end" />
  </bpmn:process>
</bpmn:definitions>"""

PATHS = {
    "no-extra": {"title": "Без доплати", "process": "issue", "phases": ["order", "deposit", "close"],
                 "path": {"extra": "f_none"}},
    "with-extra": {"title": "З доплатою", "process": "issue", "phases": ["order", "deposit", "charge", "close"],
                   "path": {"extra": "charge"}},
}


def test_two_tests_follow_different_paths_through_one_process():
    assert process_bindings(SHARED, PATHS) == ["no-extra", "with-extra"]
    first = phase_coverage(SHARED, PATHS, "no-extra")
    second = phase_coverage(SHARED, PATHS, "with-extra")
    assert first["covered"] and second["covered"], (first["errors"], second["errors"])
    assert first["mode"] == "path" and "charge" not in [item["phase"] for item in first["phases"]]
    assert "f_none" in first["visited"]["flows"] and "f_extra" not in first["visited"]["flows"]


def test_workflow_coverage_lists_what_no_test_passes():
    only_first = {"no-extra": PATHS["no-extra"]}
    coverage = workflow_coverage(SHARED, only_first)
    assert coverage["covered"] is True
    assert coverage["gaps"] == {"tasks": ["charge"], "branches": ["f_extra"]}
    assert any("charge" in text for text in coverage["warnings"])
    assert workflow_coverage(SHARED, PATHS)["gaps"] == {"tasks": [], "branches": []}


def test_path_errors_name_the_problem():
    wrong_phase = {"x": {**PATHS["no-extra"], "phases": ["order", "charge"]}}
    assert "поза обраним шляхом: charge" in " ".join(phase_coverage(SHARED, wrong_phase, "x")["errors"])
    no_choice = {"x": {**PATHS["no-extra"], "path": {}}}
    assert "не задано гілку" in " ".join(phase_coverage(SHARED, no_choice, "x")["errors"])
    bad_branch = {"x": {**PATHS["no-extra"], "path": {"extra": "nowhere"}}}
    assert "не має гілки" in " ".join(phase_coverage(SHARED, bad_branch, "x")["errors"])
    stray = {"x": {**PATHS["no-extra"], "path": {"extra": "f_none", "typo": "f1"}}}
    assert "не стосується пройденого шляху: typo" in " ".join(phase_coverage(SHARED, stray, "x")["errors"])
    other = {"x": {**PATHS["no-extra"], "process": "return"}}
    assert "проходить процес «return»" in " ".join(phase_coverage(SHARED, other, "x")["errors"])
    missing = {"x": {**PATHS["no-extra"], "phases": ["order", "nope"]}}
    assert "без елемента BPMN: nope" in " ".join(phase_coverage(SHARED, missing, "x")["errors"])


def test_phase_the_test_does_not_check_is_only_a_warning():
    partial = {"x": {**PATHS["no-extra"], "phases": ["order", "close"]}}
    coverage = phase_coverage(SHARED, partial, "x")
    assert coverage["covered"] is True
    assert "deposit" in " ".join(coverage["warnings"])


CALLER = """<?xml version="1.0" encoding="UTF-8"?>
<bpmn:definitions xmlns:bpmn="http://www.omg.org/spec/BPMN/20100524/MODEL" xmlns:aftertime="https://aftertime.local/bpmn" id="d2">
  <bpmn:process id="rental">
    <bpmn:startEvent id="s" />
    <bpmn:callActivity id="take-deposit" calledElement="deposit-sub" />
    <bpmn:userTask id="give" aftertime:testPhase="give" />
    <bpmn:endEvent id="e" />
    <bpmn:sequenceFlow id="a" sourceRef="s" targetRef="take-deposit" />
    <bpmn:sequenceFlow id="b" sourceRef="take-deposit" targetRef="give" />
    <bpmn:sequenceFlow id="c" sourceRef="give" targetRef="e" />
  </bpmn:process>
</bpmn:definitions>"""

CALLED = """<?xml version="1.0" encoding="UTF-8"?>
<bpmn:definitions xmlns:bpmn="http://www.omg.org/spec/BPMN/20100524/MODEL" xmlns:aftertime="https://aftertime.local/bpmn" id="d3">
  <bpmn:process id="deposit-sub">
    <bpmn:startEvent id="s" />
    <bpmn:serviceTask id="card" aftertime:testPhase="block" />
    <bpmn:endEvent id="e" />
    <bpmn:sequenceFlow id="a" sourceRef="s" targetRef="card" />
    <bpmn:sequenceFlow id="b" sourceRef="card" targetRef="e" />
  </bpmn:process>
</bpmn:definitions>"""


def test_phases_of_a_called_process_are_addressed_by_its_id():
    binding = {"case": {"process": "rental", "phases": ["deposit-sub.block", "give"]}}
    covered = phase_coverage(CALLER, binding, "case", {"deposit-sub": CALLED})
    assert covered["covered"] is True, covered["errors"]
    absent = phase_coverage(CALLER, binding, "case", {})
    assert "не знайдено серед опублікованих" in " ".join(absent["warnings"])
    assert "поза обраним шляхом: deposit-sub.block" in " ".join(absent["errors"])
    loop = parse_graph(CALLED, "deposit-sub")
    assert walk_path(loop, {}, {"deposit-sub": CALLED})["phases"] == ["block"]


def test_one_to_one_binding_is_unchanged_next_to_path_bindings():
    both = {**BINDINGS, **PATHS}
    xml = process()
    assert process_bindings(xml, both) == ["order-flow"]
    assert workflow_coverage(xml, both) == phase_coverage(xml, both)


def test_old_process_loses_a_test_that_moved_to_a_shared_process():
    moved = {"order-flow": {**PATHS["no-extra"], "phases": ["order", "deposit", "close"]}}
    assert process_bindings(process(), moved) == []
    assert process_bindings(SHARED, moved) == ["order-flow"]
