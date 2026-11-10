"""Published processes by BPMN process id, for ``callActivity`` calls inside a path through a process."""

from __future__ import annotations

from xml.etree import ElementTree

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import LabWorkflowVersion
from app.services.bpmn_profile import BPMN_NS


def published_library(db: Session, bpmn_xml: str | None = None) -> dict[str, str]:
    """Process id → BPMN document of its newest published version.

    With ``bpmn_xml`` the query is skipped when that process calls nothing.
    """
    if bpmn_xml is not None and "callActivity" not in bpmn_xml:
        return {}
    library: dict[str, str] = {}
    versions = db.scalars(
        select(LabWorkflowVersion)
        .where(LabWorkflowVersion.status == "published")
        .order_by(LabWorkflowVersion.published_at.asc().nulls_first())
    )
    for version in versions:
        if not version.bpmn_xml:
            continue
        try:
            root = ElementTree.fromstring(version.bpmn_xml)
        except ElementTree.ParseError:
            continue
        for process in root.iter(f"{{{BPMN_NS}}}process"):
            library[process.get("id")] = version.bpmn_xml
    return library
