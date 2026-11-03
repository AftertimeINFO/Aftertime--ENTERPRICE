"""Model packs: which domain models this Aftertime has, and what belongs to each."""

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import LabPack, LabProject, LabWorkflow
from app.pack_install import install_pack
from app.services.packs import discover

router = APIRouter(prefix="/api/v1/lab/packs", tags=["packs"])


@router.get("")
def packs(db: Session = Depends(get_db)) -> list[dict[str, Any]]:
    found = discover()
    rows = {row.id: row for row in db.scalars(select(LabPack))}
    projects: dict[str, list[str]] = {}
    for project_id, pack_id in db.execute(select(LabProject.id, LabProject.pack_id)):
        projects.setdefault(pack_id or "", []).append(project_id)
    workflows: dict[str, list[str]] = {}
    for workflow_id, pack_id in db.execute(select(LabWorkflow.id, LabWorkflow.pack_id)):
        workflows.setdefault(pack_id or "", []).append(workflow_id)
    result = []
    for pack_id in sorted(set(found) | set(rows)):
        pack, row = found.get(pack_id), rows.get(pack_id)
        result.append({
            "id": pack_id,
            "title": (row.title if row else pack.title),
            "version": (row.version if row else pack.version),
            "description": (row.description if row else pack.description),
            "available": pack is not None,
            "available_version": pack.version if pack else None,
            "installed": row is not None,
            "enabled": bool(row.enabled) if row else False,
            "report": row.report if row else {},
            "projects": sorted(projects.get(pack_id, [])),
            "workflows": sorted(workflows.get(pack_id, [])),
            "contents": pack.summary() if pack else None,
        })
    unowned = sorted(workflows.get("", [])) + sorted(projects.get("", []))
    if unowned:
        result.append({"id": "", "title": "Без пакета", "installed": True, "enabled": True, "available": False,
                       "projects": sorted(projects.get("", [])), "workflows": sorted(workflows.get("", []))})
    return result


@router.post("/{pack_id}/install")
def install(pack_id: str, db: Session = Depends(get_db)) -> dict[str, Any]:
    pack = discover().get(pack_id)
    if not pack:
        raise HTTPException(404, "Пакет не знайдено в теці пакетів")
    return install_pack(db, pack)


@router.patch("/{pack_id}")
def update(pack_id: str, body: dict[str, Any], db: Session = Depends(get_db)) -> dict[str, Any]:
    row = db.get(LabPack, pack_id)
    if not row:
        raise HTTPException(404, "Пакет не встановлено")
    if "enabled" in body:
        row.enabled = bool(body["enabled"])
    db.commit()
    return {"id": row.id, "enabled": row.enabled}
