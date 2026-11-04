"""Test reports in the Allure results format: import, categories, statistics.

A launch is one ``allure-results`` directory: ``*-result.json`` files plus
optional ``categories.json``, ``environment.properties`` and ``executor.json``.
Attachments are referenced by their ``source`` file name and stored separately.
"""

from __future__ import annotations

import os
import re
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Iterator

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import ReportAttachment, ReportLaunch, ReportResult

STATUSES = ("passed", "failed", "broken", "skipped", "unknown")
# Allure's own fallback categories when no custom category matches.
DEFAULT_CATEGORIES = {"failed": "Дефекти продукту", "broken": "Дефекти тестів"}
SOURCE_RE = re.compile(r"^[A-Za-z0-9._-]{1,200}$")


def reports_dir() -> Path:
    return Path(os.environ.get("REPORTS_DIR", "/data/reports"))


def ms_to_datetime(value: Any) -> datetime | None:
    try:
        return datetime.fromtimestamp(int(value) / 1000, UTC)
    except (TypeError, ValueError, OverflowError, OSError):
        return None


def label(result: dict[str, Any], name: str) -> str:
    return next((item.get("value", "") for item in result.get("labels") or [] if item.get("name") == name), "")


def categorize(result: dict[str, Any], categories: list[dict[str, Any]]) -> str | None:
    status = result.get("status")
    details = result.get("statusDetails") or {}
    message, trace = details.get("message") or "", details.get("trace") or ""
    for category in categories:
        if status not in category.get("matchedStatuses", ["failed", "broken"]):
            continue
        try:
            if category.get("messageRegex") and not re.fullmatch(category["messageRegex"], message, re.S):
                continue
            if category.get("traceRegex") and not re.fullmatch(category["traceRegex"], trace, re.S):
                continue
        except re.error:
            continue
        return category.get("name")
    return DEFAULT_CATEGORIES.get(status)


def attachments_of(node: dict[str, Any]) -> Iterator[dict[str, Any]]:
    yield from node.get("attachments") or []
    for step in node.get("steps") or []:
        yield from attachments_of(step)


def stats(results: list[dict[str, Any]]) -> dict[str, Any]:
    counts = Counter(result.get("status", "unknown") for result in results)
    starts = [result["start"] for result in results if result.get("start")]
    stops = [result["stop"] for result in results if result.get("stop")]
    return {
        **{status: counts.get(status, 0) for status in STATUSES},
        "total": len(results),
        "duration_ms": (max(stops) - min(starts)) if starts and stops else 0,
    }


def import_launch(db: Session, payload: dict[str, Any]) -> dict[str, Any]:
    """Store one launch; importing the same name again changes nothing."""
    name = str(payload["name"])
    results = [item for item in payload.get("results") or [] if isinstance(item, dict) and item.get("uuid")]
    launch = db.scalar(select(ReportLaunch).where(ReportLaunch.name == name))
    created = launch is None
    if created:
        categories = payload.get("categories") or []
        summary = stats(results)
        starts = [item["start"] for item in results if item.get("start")]
        stops = [item["stop"] for item in results if item.get("stop")]
        launch = ReportLaunch(
            name=name,
            executor=payload.get("executor") or {},
            environment=payload.get("environment") or {},
            categories=categories,
            stats=summary,
            start=ms_to_datetime(min(starts)) if starts else None,
            stop=ms_to_datetime(max(stops)) if stops else None,
            e2e_run_id=payload.get("e2e_run_id"),
        )
        db.add(launch)
        db.flush()
        known = set(db.scalars(select(ReportResult.id).where(ReportResult.id.in_([item["uuid"] for item in results]))))
        for item in results:
            if item["uuid"] in known:
                continue
            details = item.get("statusDetails") or {}
            db.add(ReportResult(
                id=item["uuid"],
                launch_id=launch.id,
                history_id=item.get("historyId") or item.get("fullName") or item["uuid"],
                full_name=item.get("fullName") or "",
                name=item.get("name") or "",
                status=item.get("status") or "unknown",
                parent_suite=label(item, "parentSuite"),
                suite=label(item, "suite"),
                sub_suite=label(item, "subSuite"),
                category=categorize(item, categories),
                message=(details.get("message") or "")[:4000],
                start=ms_to_datetime(item.get("start")),
                duration_ms=max(0, int(item.get("stop") or 0) - int(item.get("start") or 0)),
                data=item,
            ))
    sources: dict[str, str] = {}
    for item in results:
        for attachment in attachments_of(item):
            source = attachment.get("source", "")
            if SOURCE_RE.match(source):
                sources[source] = attachment.get("type") or "application/octet-stream"
    stored = {
        row.source: row
        for row in db.scalars(select(ReportAttachment).where(ReportAttachment.source.in_(list(sources))))
    }
    for source, content_type in sources.items():
        if source not in stored:
            db.add(ReportAttachment(source=source, content_type=content_type))
    db.commit()
    needed = sorted(source for source in sources if source not in stored or not stored[source].stored)
    if created and launch.e2e_run_id:
        from app.services.report_phases import apply_to_run

        apply_to_run(db, launch)
    return {"launch_id": launch.id, "created": created, "attachments_needed": needed}


def store_attachment(db: Session, source: str, body: bytes) -> ReportAttachment:
    row = db.get(ReportAttachment, source)
    if row is None:
        raise KeyError(source)
    directory = reports_dir()
    directory.mkdir(parents=True, exist_ok=True)
    (directory / source).write_bytes(body)
    row.size = len(body)
    row.stored = True
    db.commit()
    return row
