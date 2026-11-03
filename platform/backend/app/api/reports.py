"""Test reports: launches, latest results, suites, categories, test details, attachments."""

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import ReportArchivedTest, ReportAttachment, ReportLaunch, ReportResult
from app.services.reports import (
    SOURCE_RE, STATUSES, attachments_of, import_launch, reports_dir, store_attachment,
)
from app.services.test_bindings import load_bindings

router = APIRouter(prefix="/api/v1/lab/reports", tags=["reports"])

LIGHT_COLUMNS = (
    ReportResult.id, ReportResult.launch_id, ReportResult.history_id, ReportResult.full_name,
    ReportResult.name, ReportResult.status, ReportResult.parent_suite, ReportResult.suite,
    ReportResult.sub_suite, ReportResult.category, ReportResult.message, ReportResult.start,
    ReportResult.duration_ms,
)


def _launch_row(launch: ReportLaunch) -> dict[str, Any]:
    return {
        "id": launch.id, "name": launch.name, "executor": launch.executor, "environment": launch.environment,
        "stats": launch.stats, "start": launch.start, "stop": launch.stop, "e2e_run_id": launch.e2e_run_id,
    }


def _scope(launch_id: str | None, include_archived: bool = False):
    """Results of one launch, or the latest result of every test (like Allure's merged report).

    Archived tests (removed from the suites) stay in launches and history but leave the latest view.
    """
    if launch_id:
        return select(*LIGHT_COLUMNS).where(ReportResult.launch_id == launch_id)
    latest = (
        select(ReportResult.id)
        .distinct(ReportResult.history_id)
        .order_by(ReportResult.history_id, ReportResult.start.desc().nulls_last())
    )
    query = select(*LIGHT_COLUMNS).where(ReportResult.id.in_(latest))
    if not include_archived:
        query = query.where(ReportResult.history_id.not_in(select(ReportArchivedTest.history_id)))
    return query


def _bound_processes() -> dict[str, str]:
    """Test case id → binding id, so a result can open its BPMN process."""
    return {
        case: binding_id
        for binding_id, binding in load_bindings().items()
        for case in (binding.get("runner") or {}).get("cases", [])
    }


@router.post("/launches")
def create_launch(body: dict[str, Any], db: Session = Depends(get_db)) -> dict[str, Any]:
    if not body.get("name"):
        raise HTTPException(422, "name is required")
    return import_launch(db, body)


@router.put("/attachments/{source}")
async def upload_attachment(source: str, request: Request, db: Session = Depends(get_db)) -> dict[str, Any]:
    if not SOURCE_RE.match(source):
        raise HTTPException(422, "invalid attachment name")
    try:
        row = store_attachment(db, source, await request.body())
    except KeyError:
        raise HTTPException(404, "attachment is not referenced by any imported result") from None
    return {"source": row.source, "size": row.size}


@router.get("/attachments/{source}")
def attachment(source: str, db: Session = Depends(get_db)) -> FileResponse:
    row = db.get(ReportAttachment, source) if SOURCE_RE.match(source) else None
    path = reports_dir() / source
    if not row or not row.stored or not path.exists():
        raise HTTPException(404, "attachment not found")
    return FileResponse(path, media_type=row.content_type)


@router.get("/launches")
def launches(limit: int = 100, db: Session = Depends(get_db)) -> list[dict[str, Any]]:
    rows = db.scalars(select(ReportLaunch).order_by(ReportLaunch.start.desc().nulls_last()).limit(limit))
    return [_launch_row(row) for row in rows]


@router.get("/overview")
def overview(launch_id: str | None = None, include_archived: bool = False, db: Session = Depends(get_db)) -> dict[str, Any]:
    scope = _scope(launch_id, include_archived).subquery()
    counts = dict(db.execute(select(scope.c.status, func.count()).group_by(scope.c.status)).all())
    categories = db.execute(
        select(scope.c.category, func.count()).where(scope.c.category.is_not(None))
        .group_by(scope.c.category).order_by(func.count().desc())
    ).all()
    span = db.execute(select(func.min(scope.c.start), func.sum(scope.c.duration_ms))).one()
    trend = list(db.scalars(select(ReportLaunch).order_by(ReportLaunch.start.desc().nulls_last()).limit(30)))
    launch = db.get(ReportLaunch, launch_id) if launch_id else None
    return {
        "launch": _launch_row(launch) if launch else None,
        "stats": {**{status: counts.get(status, 0) for status in STATUSES}, "total": sum(counts.values())},
        "categories": [{"name": name, "count": count} for name, count in categories],
        "start": span[0],
        "duration_ms": int(span[1] or 0),
        "trend": [{"id": row.id, "name": row.name, "start": row.start, "stats": row.stats} for row in reversed(trend)],
        "launches": db.scalar(select(func.count()).select_from(ReportLaunch)),
    }


@router.get("/results")
def results(
    launch_id: str | None = None,
    status: str | None = None,
    category: str | None = None,
    q: str | None = None,
    include_archived: bool = False,
    db: Session = Depends(get_db),
) -> list[dict[str, Any]]:
    scope = _scope(launch_id, include_archived).subquery()
    query = select(scope)
    if status:
        query = query.where(scope.c.status.in_(status.split(",")))
    if category:
        query = query.where(scope.c.category == category)
    if q:
        pattern = f"%{q}%"
        query = query.where(or_(scope.c.name.ilike(pattern), scope.c.full_name.ilike(pattern), scope.c.message.ilike(pattern)))
    bound = _bound_processes()
    archived = set(db.scalars(select(ReportArchivedTest.history_id)))
    rows = db.execute(query.order_by(scope.c.parent_suite, scope.c.suite, scope.c.name)).mappings()
    return [{**row, "binding": bound.get(row["full_name"]), "archived": row["history_id"] in archived} for row in rows]


@router.get("/results/{result_id}")
def result(result_id: str, db: Session = Depends(get_db)) -> dict[str, Any]:
    row = db.get(ReportResult, result_id)
    if not row:
        raise HTTPException(404, "result not found")
    history = db.execute(
        select(ReportResult.id, ReportResult.status, ReportResult.start, ReportResult.duration_ms,
               ReportResult.message, ReportLaunch.name.label("launch"))
        .join(ReportLaunch, ReportLaunch.id == ReportResult.launch_id)
        .where(ReportResult.history_id == row.history_id)
        .order_by(ReportResult.start.desc().nulls_last())
        .limit(30)
    ).mappings().all()
    sources = [item.get("source", "") for item in attachments_of(row.data)]
    stored = set(db.scalars(
        select(ReportAttachment.source).where(ReportAttachment.source.in_(sources), ReportAttachment.stored.is_(True))
    ))
    launch = db.get(ReportLaunch, row.launch_id)
    return {
        "id": row.id, "launch": _launch_row(launch), "history_id": row.history_id, "full_name": row.full_name,
        "name": row.name, "status": row.status, "category": row.category, "duration_ms": row.duration_ms,
        "start": row.start, "data": row.data, "stored_attachments": sorted(stored),
        "history": [dict(item) for item in history],
        "binding": _bound_processes().get(row.full_name),
        "archived": db.get(ReportArchivedTest, row.history_id) is not None,
    }


@router.post("/archive")
def archive_test(body: dict[str, Any], db: Session = Depends(get_db)) -> dict[str, Any]:
    history_id = str(body.get("history_id") or "")
    if not history_id:
        raise HTTPException(422, "history_id is required")
    if not db.get(ReportArchivedTest, history_id):
        db.add(ReportArchivedTest(history_id=history_id, name=str(body.get("name") or "")))
        db.commit()
    return {"history_id": history_id, "archived": True}


@router.delete("/archive/{history_id}")
def restore_test(history_id: str, db: Session = Depends(get_db)) -> dict[str, Any]:
    row = db.get(ReportArchivedTest, history_id)
    if row:
        db.delete(row)
        db.commit()
    return {"history_id": history_id, "archived": False}


@router.get("/archive")
def archived_tests(db: Session = Depends(get_db)) -> list[dict[str, Any]]:
    rows = db.scalars(select(ReportArchivedTest).order_by(ReportArchivedTest.archived_at.desc()))
    return [{"history_id": row.history_id, "name": row.name, "archived_at": row.archived_at} for row in rows]
