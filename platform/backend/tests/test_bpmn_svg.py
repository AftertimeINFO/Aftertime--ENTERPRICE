from xml.etree import ElementTree

import pytest
from fastapi import HTTPException

from app.api.model_lab import (
    clone_workflow,
    create_workflow,
    export_workflow,
    save_workflow_bpmn,
    save_workflow_svg,
    workflow,
    workflow_svg,
)
from app.db import SessionLocal
from app.models import LabWorkflow
from app.services.bpmn_profile import build_bpmn_xml
from app.services.bpmn_svg import (
    AFTERTIME_NS,
    SVG_NS,
    SvgPresentationError,
    annotate_svg,
    bpmn_sha256,
    check_svg,
    drawn_elements,
)

NODES = [
    {"node_key": "start", "block_type": "start_event", "name": "Клієнт звернувся", "x": 80, "y": 120, "config": {"event_definition": "message", "event_type": "rental.requested"}},
    {"node_key": "issue", "block_type": "user_task", "name": "Видати спорядження", "x": 280, "y": 120, "config": {}},
    {"node_key": "end", "block_type": "end_event", "name": "Спорядження видано", "x": 500, "y": 120, "config": {"event_definition": "none"}},
]
FLOWS = [
    {"flow_key": "f1", "source_key": "start", "target_key": "issue"},
    {"flow_key": "f2", "source_key": "issue", "target_key": "end"},
]


def bpmn(name: str = "Видача спорядження") -> str:
    return build_bpmn_xml("rental-issue", name, NODES, FLOWS)


def bpmn_js_svg(xml: str, skip: frozenset[str] = frozenset()) -> str:
    """The shape of ``modeler.saveSVG()``: one ``djs-element`` group per drawn element, plus labels."""
    groups = []
    for element_id in drawn_elements(xml):
        if element_id in skip:
            continue
        groups.append(f'<g class="djs-element" data-element-id="{element_id}"><rect width="10" height="10"/></g>')
        groups.append(f'<g class="djs-element djs-shape" data-element-id="{element_id}_label"><text>x</text></g>')
    return f'<svg xmlns="{SVG_NS}" width="600" height="300"><defs/>{"".join(groups)}</svg>'


def test_annotation_marks_every_element_and_records_provenance():
    xml = bpmn()
    svg, report = annotate_svg(bpmn_js_svg(xml), xml, process_id="rental-issue", version_id="v1", version=3)

    root = ElementTree.fromstring(svg)
    shapes = {g.get("data-element-id"): g for g in root.iter() if g.get("data-element-id")}
    assert report["status"] == "synced"
    assert report["elements"] == len(drawn_elements(xml)) == 5
    assert shapes["issue"].get("data-element-type") == "bpmn:userTask"
    assert shapes["issue_label"].get("data-label-for") == "issue"
    presentation = root.find(f"{{{SVG_NS}}}metadata/{{{AFTERTIME_NS}}}presentation")
    assert presentation.get("bpmn-sha256") == bpmn_sha256(xml)
    assert presentation.get("process") == "rental-issue"
    assert presentation.get("version") == "3"


def test_check_detects_stale_and_incomplete_drawings():
    xml = bpmn()
    svg, _ = annotate_svg(bpmn_js_svg(xml), xml, process_id="p", version_id="v", version=1)
    assert check_svg(svg, xml)["status"] == "synced"
    assert check_svg(svg, bpmn("Інша назва"))["status"] == "stale"

    partial, report = annotate_svg(bpmn_js_svg(xml, frozenset({"issue"})), xml, process_id="p", version_id="v", version=1)
    assert report["status"] == "incomplete"
    assert report["missing"] == ["issue"]
    assert check_svg(partial, xml)["missing"] == ["issue"]


def test_reannotation_keeps_a_single_presentation_record():
    xml = bpmn()
    once, _ = annotate_svg(bpmn_js_svg(xml), xml, process_id="p", version_id="v", version=1)
    twice, _ = annotate_svg(once, xml, process_id="p", version_id="v2", version=2)
    records = ElementTree.fromstring(twice).findall(f".//{{{AFTERTIME_NS}}}presentation")
    assert len(records) == 1
    assert records[0].get("version-id") == "v2"


def test_rejects_documents_that_are_not_svg():
    with pytest.raises(SvgPresentationError):
        annotate_svg("<html/>", bpmn(), process_id="p", version_id="v", version=1)


@pytest.fixture
def draft():
    with SessionLocal() as db:
        created = create_workflow({"name": "Тест SVG-презентації"}, db)
        yield created["version"]["id"]
        db.delete(db.get(LabWorkflow, created["version"]["workflow_id"]))
        db.commit()


def test_api_keeps_svg_in_step_with_bpmn(draft):
    with SessionLocal() as db:
        assert workflow(draft, db)["presentation"]["status"] == "missing"

        xml = bpmn()
        saved = save_workflow_bpmn(draft, {"bpmn_xml": xml, "bpmn_svg": bpmn_js_svg(xml)}, db)
        assert saved["presentation"]["status"] == "synced"
        assert workflow_svg(draft, db).headers["X-Aftertime-Svg-Status"] == "synced"

        exported = export_workflow(draft, db)
        assert check_svg(exported["bpmn_svg"], exported["bpmn_xml"])["status"] == "synced"

        changed = bpmn("Видача спорядження у прокат")
        assert save_workflow_bpmn(draft, {"bpmn_xml": changed}, db)["presentation"]["status"] == "stale"

        with pytest.raises(HTTPException) as conflict:
            save_workflow_svg(draft, {"bpmn_svg": bpmn_js_svg(changed), "bpmn_sha256": bpmn_sha256(xml)}, db)
        assert conflict.value.status_code == 409

        repaired = save_workflow_svg(draft, {"bpmn_svg": bpmn_js_svg(changed), "bpmn_sha256": bpmn_sha256(changed)}, db)
        assert repaired["status"] == "synced"

        copy = clone_workflow(draft, db)
        assert copy["presentation"]["status"] == "synced"
