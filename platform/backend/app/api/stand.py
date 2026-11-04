"""Preparation of the test stand: its plan, the files behind it and their saved versions."""

import json
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import LabE2eRun, LabStandFileVersion
from app.services import stand_prep
from app.services.stand_prep import StandError

router = APIRouter(prefix="/api/v1/lab/stand", tags=["stand"])

ACTIVE = ("queued", "claimed", "waiting", "running")


def _fail(error: StandError) -> HTTPException:
    return HTTPException(error.status, error.message)


def _busy(db: Session) -> dict[str, Any] | None:
    run = db.scalars(select(LabE2eRun).where(LabE2eRun.status.in_(ACTIVE)).order_by(LabE2eRun.created_at.desc())).first()
    return {"id": run.id, "status": run.status, "kind": run.kind} if run else None


@router.get("/preparation")
def preparation(db: Session = Depends(get_db)) -> dict[str, Any]:
    """The plan with what each step did lately, the stand's cases (for "which steps does this test get") and the lock."""
    config = stand_prep.settings()
    if not config:
        return {"configured": False}
    try:
        file = stand_prep.read_file(config, config["plan"])
    except StandError as error:
        return {"configured": True, "error": error.message, "plan_path": config["plan"]}
    try:
        plan = json.loads(file["text"])
    except json.JSONDecodeError as error:
        plan = None
        problem = f"План не є коректним JSON: рядок {error.lineno}, {error.msg}"
    else:
        problem = None
    cases: list[dict[str, Any]] = []
    if config.get("cases"):
        try:
            data = json.loads(stand_prep.read_file(config, config["cases"])["text"])
            cases = data.get("cases", data) if isinstance(data, dict) else data
        except (StandError, json.JSONDecodeError):
            cases = []
    timelines = [run.timeline or [] for run in db.scalars(
        select(LabE2eRun).where(LabE2eRun.timeline.is_not(None)).order_by(LabE2eRun.created_at.desc()).limit(40))]
    return {
        "configured": True,
        "plan_path": config["plan"],
        "scripts": config.get("scripts", ""),
        "plan": plan,
        "error": problem,
        "text": file["text"],
        "sha": file["sha"],
        "cases": cases,
        "stats": stand_prep.step_stats([timeline for timeline in timelines if any(item.get("prep") for item in timeline)]),
        "busy": _busy(db),
    }


@router.get("/files")
def read_file(path: str) -> dict[str, Any]:
    config = stand_prep.settings()
    if not config:
        raise HTTPException(404, "Підготовку стенда не налаштовано")
    try:
        return stand_prep.read_file(config, path)
    except StandError as error:
        raise _fail(error) from error


@router.put("/files")
def save_file(body: dict[str, Any], db: Session = Depends(get_db)) -> dict[str, Any]:
    """Write a stand file. Refused while a run is on the stand or if the file changed since ``sha`` was read."""
    path, text, base = str(body.get("path") or ""), body.get("text"), body.get("sha")
    if not isinstance(text, str):
        raise HTTPException(422, "Немає вмісту файлу")
    try:
        config = stand_prep._required()
        busy = _busy(db)
        if busy:
            raise StandError(409, "На стенді йде прогін: зміни підготовки збережіть після нього")
        current = stand_prep.read_file(config, path)
        if base and base != current["sha"]:
            raise StandError(409, "Файл змінився на диску після відкриття: відкрийте його знову й повторіть зміни")
        if current["path"] == config["plan"]:
            stand_prep.check_plan(config, text)
        data = stand_prep.encode(text, current["bom"], current["eol"])
    except StandError as error:
        raise _fail(error) from error
    now = datetime.now(UTC)
    if not db.scalars(select(LabStandFileVersion.id).where(LabStandFileVersion.path == current["path"]).limit(1)).first():
        db.add(LabStandFileVersion(path=current["path"], content=current["text"], sha=current["sha"],
                                   note="До першої зміни з Aftertime", saved_at=now - timedelta(milliseconds=1)))
    target = stand_prep.resolve(config, current["path"])
    target.write_bytes(data)
    sha = sha256(data).hexdigest()
    version = LabStandFileVersion(path=current["path"], content=text.replace("\r\n", "\n"), sha=sha, note=str(body.get("note") or "").strip()[:200], saved_at=now)
    db.add(version)
    db.commit()
    return {**stand_prep.read_file(config, current["path"]), "version": version.id}


@router.get("/files/versions")
def file_versions(path: str, db: Session = Depends(get_db)) -> list[dict[str, Any]]:
    rows = db.scalars(select(LabStandFileVersion).where(LabStandFileVersion.path == path.replace("\\", "/"))
                      .order_by(LabStandFileVersion.saved_at.desc()).limit(50))
    return [{"id": row.id, "sha": row.sha, "note": row.note, "saved_at": row.saved_at, "lines": row.content.count("\n") + 1} for row in rows]


@router.get("/files/versions/{version_id}")
def file_version(version_id: str, db: Session = Depends(get_db)) -> dict[str, Any]:
    row = db.get(LabStandFileVersion, version_id)
    if not row:
        raise HTTPException(404, "Версію не знайдено")
    return {"id": row.id, "path": row.path, "text": row.content, "sha": row.sha, "note": row.note, "saved_at": row.saved_at}
