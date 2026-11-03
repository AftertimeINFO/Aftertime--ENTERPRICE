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
    assert load_bindings() == BINDINGS
    monkeypatch.setenv("AFTERTIME_CONFIG_DIR", str(tmp_path / "absent"))
    assert load_bindings() == {}
