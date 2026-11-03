"""Phase states of an end-to-end case, derived from the proofs its runner leaves.

A runner proves some phases with files (``...-<phase>.json`` with an optional
``"verified"`` flag, ``...-<phase>-error.txt`` on failure).  Phases without a
proof of their own are inferred from their neighbours: a later proven phase
implies every earlier one passed.  The case outcome closes the remaining phases.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

PASSED, FAILED, ACTIVE, PENDING, NOT_REACHED, STOPPED = (
    "passed", "failed", "active", "pending", "not-reached", "stopped",
)


@dataclass(frozen=True)
class Proof:
    phase: str
    passed: bool
    source: str
    detail: str = ""


def read_proofs(
    entries: Iterable[tuple[str, str]],
    proof_pattern: str,
    error_pattern: str | None = None,
) -> dict[str, Proof]:
    """Proofs from ``(file name, file text)`` pairs; a failure outranks a success."""
    proof_re = re.compile(proof_pattern)
    error_re = re.compile(error_pattern) if error_pattern else None
    proofs: dict[str, Proof] = {}
    for name, text in entries:
        if error_re and (match := error_re.search(name)):
            phase = match.group("phase")
            proofs[phase] = Proof(phase, False, name, text.strip()[:500])
            continue
        match = proof_re.search(name)
        if not match:
            continue
        phase = match.group("phase")
        try:
            data = json.loads(text) if text.strip() else {}
        except json.JSONDecodeError:
            data = {}
        passed = data.get("verified", True) is not False if isinstance(data, dict) else True
        detail = str(data.get("defect", "")) if isinstance(data, dict) else ""
        if phase not in proofs or proofs[phase].passed:
            proofs[phase] = Proof(phase, passed, name, detail)
    return proofs


def folder_entries(folder: Path) -> list[tuple[str, str]]:
    return [
        (path.name, path.read_text(encoding="utf-8-sig", errors="replace"))
        for path in sorted(folder.iterdir())
        if path.is_file() and path.suffix in {".json", ".txt"}
    ]


def phase_states(
    phases: list[str],
    proofs: dict[str, Proof],
    outcome: str | None = None,
) -> list[dict[str, Any]]:
    """``outcome`` is None while the case runs, else passed / failed / stopped."""
    proven = [index for index, phase in enumerate(phases) if phase in proofs]
    failed = next((index for index in proven if not proofs[phases[index]].passed), None)
    last = failed if failed is not None else (proven[-1] if proven else -1)
    states: list[dict[str, Any]] = []
    for index, phase in enumerate(phases):
        proof = proofs.get(phase)
        item: dict[str, Any] = {"phase": phase, "inferred": proof is None}
        if proof:
            item["evidence"] = proof.source
            if proof.detail:
                item["detail"] = proof.detail
        if index < last:
            item["state"] = PASSED if (proof is None or proof.passed) else FAILED
        elif index == last:
            item["state"] = PASSED if proof.passed else FAILED
        elif failed is not None:
            item["state"] = NOT_REACHED
        elif outcome is None:
            item["state"] = ACTIVE if index == last + 1 else PENDING
        elif outcome == PASSED:
            item["state"] = PASSED
        elif index == last + 1:
            item["state"] = FAILED if outcome == FAILED else STOPPED
        else:
            item["state"] = NOT_REACHED
        states.append(item)
    return states


def changes(before: list[dict[str, Any]], after: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Phases whose state changed, in process order."""
    previous = {item["phase"]: item["state"] for item in before}
    return [item for item in after if previous.get(item["phase"]) != item["state"]]
