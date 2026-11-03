"""Install model packs: register them, run their installers, import their processes.

    python -m app.pack_install            # every pack in $AFTERTIME_PACKS_DIR
    python -m app.pack_install rental     # only the named packs
"""

from __future__ import annotations

import sys
from datetime import UTC, datetime
from typing import Any

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.model_lab import clone_workflow, create_workflow, publish_workflow, save_workflow_bpmn, validate_workflow
from app.db import SessionLocal
from app.models import LabPack, LabWorkflow, LabWorkflowVersion
from app.services.bpmn_svg import bpmn_sha256
from app.services.packs import Pack, discover, process_identity, run_installer


def import_process(db: Session, pack: Pack, bpmn_xml: str, publish: bool = True) -> dict[str, Any]:
    """Create or update the process whose workflow id is the BPMN process id."""
    workflow_id, name = process_identity(bpmn_xml)
    versions = list(db.scalars(select(LabWorkflowVersion).where(LabWorkflowVersion.workflow_id == workflow_id)))
    latest = max(versions, key=lambda item: item.version) if versions else None
    if latest and bpmn_sha256(latest.bpmn_xml) == bpmn_sha256(bpmn_xml) and (latest.status == "published" or not publish):
        _own(db, workflow_id, pack.id)
        return {"process": workflow_id, "version": latest.version, "action": "unchanged"}
    if latest is None:
        draft_id = create_workflow({"id": workflow_id, "name": name}, db)["version"]["id"]
    elif latest.status == "draft":
        draft_id = latest.id
    else:
        draft_id = clone_workflow(latest.id, db)["version"]["id"]
    _own(db, workflow_id, pack.id)
    save_workflow_bpmn(draft_id, {"bpmn_xml": bpmn_xml}, db)
    version = db.get(LabWorkflowVersion, draft_id)
    result: dict[str, Any] = {"process": workflow_id, "version": version.version, "action": "imported"}
    if publish:
        report = validate_workflow(draft_id, db)
        if report["valid"]:
            publish_workflow(draft_id, db)
            result["action"] = "published"
        else:
            result["errors"] = report["errors"]
    return result


def _own(db: Session, workflow_id: str, pack_id: str) -> None:
    workflow = db.get(LabWorkflow, workflow_id)
    if workflow and workflow.pack_id != pack_id:
        workflow.pack_id = pack_id
        db.commit()


def install_pack(db: Session, pack: Pack) -> dict[str, Any]:
    row = db.get(LabPack, pack.id) or LabPack(id=pack.id, title=pack.title)
    row.title, row.version, row.description, row.source = pack.title, pack.version, pack.description, str(pack.path)
    db.add(row)
    db.commit()
    report: dict[str, Any] = {"pack": pack.id, "version": pack.version, "processes": [], "installed_at": datetime.now(UTC).isoformat()}
    if pack.installer:
        report["installer"] = run_installer(pack, db)
    for file in pack.process_files():
        try:
            report["processes"].append(import_process(db, pack, file.read_text(encoding="utf-8"),
                                                      publish=pack.manifest.get("publish", True)))
        except (ValueError, HTTPException) as exc:
            db.rollback()
            detail = exc.detail if isinstance(exc, HTTPException) else str(exc)
            report["processes"].append({"file": file.name, "action": "failed", "errors": detail})
    row = db.get(LabPack, pack.id)
    row.report = report
    db.commit()
    return report


def install_all(names: list[str] | None = None) -> list[dict[str, Any]]:
    packs = discover()
    reports = []
    with SessionLocal() as db:
        for key, pack in packs.items():
            if names and key not in names:
                continue
            try:
                reports.append(install_pack(db, pack))
            except Exception as exc:  # one broken pack must not stop the API from starting
                db.rollback()
                reports.append({"pack": key, "version": pack.version, "processes": [], "error": str(exc)})
    return reports


if __name__ == "__main__":
    for item in install_all(sys.argv[1:] or None):
        actions = ", ".join(f"{process.get('process', process.get('file'))}: {process['action']}" for process in item["processes"])
        print(f"pack {item['pack']} {item['version']}: {item.get('error') or actions or 'без процесів'}")
