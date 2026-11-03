"""SVG presentation of a canonical BPMN document.

The BPMN XML stays canonical.  The SVG is a derived presentation for people and
agents: every diagram element keeps ``data-element-id`` (the BPMN id) and gets
``data-element-type`` (the BPMN tag), and ``<metadata>`` records which BPMN
document (SHA-256 of the XML) the drawing was made from.  A presentation whose
hash differs from the current XML is stale.

Standard library only: ``platform/tools/models.py`` imports this module outside
the API container.
"""

from __future__ import annotations

from hashlib import sha256
from typing import Any
from xml.etree import ElementTree

SVG_NS = "http://www.w3.org/2000/svg"
XLINK_NS = "http://www.w3.org/1999/xlink"
BPMN_NS = "http://www.omg.org/spec/BPMN/20100524/MODEL"
BPMNDI_NS = "http://www.omg.org/spec/BPMN/20100524/DI"
AFTERTIME_NS = "https://aftertime.local/bpmn"
PRESENTATION_PROFILE = "aftertime-bpmn-svg-1"

ElementTree.register_namespace("", SVG_NS)
ElementTree.register_namespace("xlink", XLINK_NS)
ElementTree.register_namespace("aftertime", AFTERTIME_NS)

_LABEL_SUFFIX = "_label"


class SvgPresentationError(ValueError):
    pass


def bpmn_sha256(bpmn_xml: str) -> str:
    return sha256(bpmn_xml.encode("utf-8")).hexdigest()


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def drawn_elements(bpmn_xml: str) -> dict[str, str]:
    """BPMN ids that have a DI shape or edge, mapped to ``bpmn:<tag>``."""
    try:
        root = ElementTree.fromstring(bpmn_xml)
    except ElementTree.ParseError as exc:
        raise SvgPresentationError(f"Некоректний BPMN XML: {exc}") from exc
    tags = {
        element.get("id"): f"bpmn:{_local(element.tag)}"
        for element in root.iter()
        if element.get("id") and element.tag.startswith(f"{{{BPMN_NS}}}")
    }
    drawn: dict[str, str] = {}
    for di in root.iter():
        if di.tag in {f"{{{BPMNDI_NS}}}BPMNShape", f"{{{BPMNDI_NS}}}BPMNEdge"}:
            ref = di.get("bpmnElement")
            if ref in tags:
                drawn[ref] = tags[ref]
    return drawn


def _svg_root(svg: str) -> ElementTree.Element:
    try:
        root = ElementTree.fromstring(svg)
    except ElementTree.ParseError as exc:
        raise SvgPresentationError(f"Некоректний SVG: {exc}") from exc
    if root.tag != f"{{{SVG_NS}}}svg":
        raise SvgPresentationError("Кореневим елементом має бути svg")
    return root


def _svg_ids(root: ElementTree.Element) -> set[str]:
    return {
        element.get("data-element-id")
        for element in root.iter()
        if element.get("data-element-id")
        and not element.get("data-element-id").endswith(_LABEL_SUFFIX)
    }


def _report(drawn: dict[str, str], svg_ids: set[str], hash_ok: bool) -> dict[str, Any]:
    missing = sorted(set(drawn) - svg_ids)
    extra = sorted(svg_ids - set(drawn))
    if not hash_ok:
        status = "stale"
    elif missing:
        status = "incomplete"
    else:
        status = "synced"
    return {"status": status, "hash_ok": hash_ok, "missing": missing, "extra": extra, "elements": len(drawn)}


def annotate_svg(
    svg: str,
    bpmn_xml: str,
    *,
    process_id: str,
    version_id: str,
    version: int,
) -> tuple[str, dict[str, Any]]:
    """Add element types and provenance metadata to an SVG exported by bpmn-js."""
    root = _svg_root(svg)
    drawn = drawn_elements(bpmn_xml)
    for element in root.iter():
        element_id = element.get("data-element-id")
        if not element_id:
            continue
        base_id = element_id.removesuffix(_LABEL_SUFFIX)
        if base_id in drawn:
            element.set("data-element-type", drawn[base_id])
            if element_id.endswith(_LABEL_SUFFIX):
                element.set("data-label-for", base_id)
    for old in root.findall(f"{{{SVG_NS}}}metadata"):
        if old.find(f"{{{AFTERTIME_NS}}}presentation") is not None:
            root.remove(old)
    metadata = ElementTree.Element(f"{{{SVG_NS}}}metadata")
    ElementTree.SubElement(
        metadata,
        f"{{{AFTERTIME_NS}}}presentation",
        {
            "profile": PRESENTATION_PROFILE,
            "process": process_id,
            "version-id": version_id,
            "version": str(version),
            "bpmn-sha256": bpmn_sha256(bpmn_xml),
        },
    )
    root.insert(0, metadata)
    annotated = ElementTree.tostring(root, encoding="unicode")
    return annotated, _report(drawn, _svg_ids(root), True)


def svg_metadata(svg: str) -> dict[str, str]:
    presentation = _svg_root(svg).find(f"{{{SVG_NS}}}metadata/{{{AFTERTIME_NS}}}presentation")
    return dict(presentation.attrib) if presentation is not None else {}


def check_svg(svg: str, bpmn_xml: str) -> dict[str, Any]:
    """Compare a presentation with the BPMN document it claims to show."""
    root = _svg_root(svg)
    meta = svg_metadata(svg)
    hash_ok = meta.get("bpmn-sha256") == bpmn_sha256(bpmn_xml)
    return {**_report(drawn_elements(bpmn_xml), _svg_ids(root), hash_ok), "metadata": meta}
