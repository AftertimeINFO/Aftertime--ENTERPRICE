"""Phases of a bound test read from the steps of its report.

Tests without proof files still show their progress on the BPMN process: the binding
says how its phases appear in the report (``steps`` in the binding)::

    {"steps": {"mode": "match", "rules": {"order": "ORDER_VERIFIED|^order:"}}}
    {"steps": {"mode": "index"}}      # the n-th top-level step is the n-th phase
    {"steps": {"mode": "outcome"}}    # only the result of the test is known

A step that matches the rule of a phase proves that phase, passed or failed as the step
is; phases before a proven one are passed. A failure the rules do not place is put on the
phase after the last proven one, the way the bridge reads proof files.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import LabE2eRun, ReportLaunch, ReportResult
from app.services.test_bindings import load_bindings

PASSED, FAILED, PENDING, NOT_REACHED, STOPPED = "passed", "failed", "pending", "not-reached", "stopped"
BAD = {"failed", "broken"}


def _flatten(steps: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []
    for step in steps or []:
        children = _flatten(step.get("steps"))
        if children and all(child.get("status") == "skipped" for child in children):
            # A group whose every step was skipped did not run, whatever status it carries.
            step = {**step, "status": "skipped"}
        found.append(step)
        found.extend(children)
    return found


def _text(step: dict[str, Any]) -> str:
    details = step.get("statusDetails") or {}
    return f"{step.get('name') or ''}\n{details.get('message') or ''}"


def _proofs(phases: list[str], config: dict[str, Any], data: dict[str, Any]) -> dict[str, dict[str, Any]]:
    mode = config.get("mode", "match")
    proofs: dict[str, dict[str, Any]] = {}
    if mode == "index":
        for phase, step in zip(phases, data.get("steps") or []):
            if step.get("status") not in BAD and step.get("status") != "passed":
                break
            proofs[phase] = {"passed": step.get("status") not in BAD, "step": step}
        return proofs
    if mode != "match":
        return proofs
    rules = []
    for phase in phases:
        pattern = (config.get("rules") or {}).get(phase)
        if pattern:
            try:
                rules.append((phase, re.compile(pattern, re.I | re.M)))
            except re.error:
                continue
    for step in _flatten(data.get("steps")):
        if step.get("status") not in BAD and step.get("status") != "passed":
            continue  # steps a stopped scenario never ran do not prove anything
        text = _text(step)
        for phase, rule in rules:
            if rule.search(text):
                proof = proofs.get(phase)
                failed = step.get("status") in BAD
                if proof is None or (failed and proof["passed"]):
                    proofs[phase] = {"passed": not failed, "step": step}
                break
    return proofs


def _first_failure_phase(phases: list[str], config: dict[str, Any], message: str) -> int | None:
    for index, phase in enumerate(phases):
        pattern = (config.get("rules") or {}).get(phase)
        if pattern and message:
            try:
                if re.search(pattern, message, re.I | re.M):
                    return index
            except re.error:
                continue
    return None


def derive_states(binding: dict[str, Any], status: str, data: dict[str, Any], message: str = "") -> list[dict[str, Any]]:
    """States of the binding phases for one report result."""
    phases = list(binding.get("phases") or [])
    config = binding.get("steps") or {"mode": "outcome"}
    proofs = _proofs(phases, config, data or {})
    proven = [index for index, phase in enumerate(phases) if phase in proofs]
    failed = next((index for index in proven if not proofs[phases[index]]["passed"]), None)
    outcome = PASSED if status == PASSED else (STOPPED if status == "skipped" else FAILED)
    if failed is None and outcome == FAILED:
        placed = _first_failure_phase(phases, config, message)
        if placed is not None and (not proven or placed > proven[-1]):
            failed = placed
            proofs[phases[placed]] = {"passed": False, "step": None}
    last = failed if failed is not None else (proven[-1] if proven else -1)
    # The deepest failing step says best what went wrong.
    failing = [step for step in _flatten((data or {}).get("steps")) if step.get("status") in BAD
               and (step.get("statusDetails") or {}).get("message")]
    reason = str((failing[-1].get("statusDetails") or {}).get("message")) if failing else message
    states: list[dict[str, Any]] = []
    for index, phase in enumerate(phases):
        proof = proofs.get(phase)
        item: dict[str, Any] = {"phase": phase, "inferred": proof is None or proof.get("step") is None}
        step = proof.get("step") if proof else None
        if step:
            item["evidence"] = str(step.get("name") or "")[:200]
            item["at"] = step.get("stop") or step.get("start")
            detail = (step.get("statusDetails") or {}).get("message")
            if detail and not proof["passed"]:
                item["detail"] = str(detail)[:500]
        elif proof and not proof["passed"] and reason:
            item["detail"] = reason[:500]
        if index < last:
            item["state"] = PASSED if (proof is None or proof["passed"]) else FAILED
        elif index == last:
            item["state"] = PASSED if proof["passed"] else FAILED
        elif failed is not None:
            item["state"] = NOT_REACHED
        elif outcome == PASSED:
            item["state"] = PASSED
        elif index == last + 1:
            item["state"] = outcome
            if reason and "detail" not in item:
                item["detail"] = reason[:500]
        else:
            item["state"] = NOT_REACHED
        states.append(item)
    return states


def timeline_of(states: list[dict[str, Any]], since: datetime | None, until: datetime | None) -> list[dict[str, Any]]:
    """Phase changes at the times of their report steps, if those times belong to the run.

    Clocks of the machines under test may be shifted by the scenario itself; a step time
    outside the run is not a time the run can be replayed by.
    """
    timeline = []
    for item in states:
        at = item.get("at")
        if not isinstance(at, (int, float)):
            continue
        moment = datetime.fromtimestamp(at / 1000, UTC)
        if since and moment < since or until and moment > until:
            continue
        timeline.append({"t": moment.isoformat(), "phase": item["phase"], "state": item["state"]})
    return sorted(timeline, key=lambda entry: entry["t"])


def apply_to_run(db: Session, launch: ReportLaunch) -> bool:
    """A report imported for a run of a bound process paints the phases the run could not prove."""
    run = db.get(LabE2eRun, launch.e2e_run_id) if launch.e2e_run_id else None
    if run is None or run.kind != "binding" or not run.binding:
        return False
    if any(item.get("evidence") for item in run.states or []):
        return False  # proof files of the stand are the better witness
    binding = load_bindings().get(run.binding)
    cases = (binding or {}).get("runner", {}).get("cases") or []
    result = db.scalar(select(ReportResult).where(ReportResult.launch_id == launch.id, ReportResult.full_name.in_(cases)))
    if binding is None or result is None:
        return False
    derived = {item["phase"]: item for item in derive_states(binding, result.status, result.data or {}, result.message)}
    before = {item["phase"]: item.get("state") for item in run.states or []}
    run.states = [{**item, **{key: value for key, value in derived.get(item["phase"], {}).items() if key != "at"}}
                  for item in run.states or []]
    changes = [entry for entry in timeline_of(list(derived.values()), run.claimed_at, run.finished_at)
               if before.get(entry["phase"]) != entry["state"]]
    now = datetime.now(UTC).isoformat()
    moved = {entry["phase"] for entry in changes}
    changes += [{"t": now, "phase": item["phase"], "state": item["state"]} for item in run.states
                if item["phase"] not in moved and before.get(item["phase"]) != item.get("state")]
    if changes:
        run.timeline = (run.timeline or []) + changes
    db.commit()
    return True
