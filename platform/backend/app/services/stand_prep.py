"""Preparation of the test stand: the plan the stand runs before its tests, and the files behind it.

The stand keeps its preparation as a plan file (an ordered list of steps, each a script with
arguments, a scope and conditions) next to its scripts.  The installation tells where they are in
``$AFTERTIME_CONFIG_DIR/e2e/preparation.json``::

    {"plan": "suite/data/prep-plan.json", "cases": "suite/data/cases.json",
     "scripts": "suite", "files": ["suite/*.ps1", "suite/lib/*.ps1", "suite/data/prep-plan.json"]}

Paths are relative to the stand directory (``$AFTERTIME_STAND_DIR``, mounted into the API).
Only files matching ``files`` are read or written.  A write keeps the file's byte-order mark and
line endings, refuses while a run is on the stand or when the file changed since it was read,
and every saved content is kept as a version.
"""

import json
import os
import re
from fnmatch import fnmatchcase
from hashlib import sha256
from pathlib import Path, PurePosixPath
from typing import Any

BOM = b"\xef\xbb\xbf"
EQUIP = (None, "ensure", "auto")
# A preparation step reported in the run log: PREP|<state>|<n>|<total>|<id>|<ms>|<changed>|<title>
PREP_LINE = re.compile(r"PREP\|(start|ok|failed|skipped)\|(\d+)\|(\d+)\|([^|]+)\|(\d*)\|([^|]*)\|(.*)$")


class StandError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status
        self.message = message


def stand_dir() -> Path:
    return Path(os.environ.get("AFTERTIME_STAND_DIR", "/stand"))


def settings() -> dict[str, Any] | None:
    file = Path(os.environ.get("AFTERTIME_CONFIG_DIR", "/config")) / "e2e" / "preparation.json"
    if not file.is_file():
        return None
    data = json.loads(file.read_text(encoding="utf-8"))
    return data if isinstance(data, dict) and data.get("plan") else None


def _required() -> dict[str, Any]:
    config = settings()
    if not config:
        raise StandError(404, "Підготовку стенда не налаштовано (e2e/preparation.json у налаштуваннях)")
    if not stand_dir().is_dir():
        raise StandError(503, "Каталог стенда недоступний для Aftertime (AFTERTIME_STAND_DIR)")
    return config


def allowed(config: dict[str, Any], path: str) -> bool:
    patterns = [config["plan"], *([config["cases"]] if config.get("cases") else []), *(config.get("files") or [])]
    return any(fnmatchcase(path, pattern) for pattern in patterns)


def resolve(config: dict[str, Any], path: str) -> Path:
    clean = PurePosixPath(path.replace("\\", "/"))
    if clean.is_absolute() or ".." in clean.parts or not allowed(config, clean.as_posix()):
        raise StandError(403, f"Файл недоступний для перегляду й змін: {path}")
    full = stand_dir() / clean
    if not full.is_file():
        raise StandError(404, f"Файл не знайдено: {path}")
    return full


def read_file(config: dict[str, Any], path: str) -> dict[str, Any]:
    raw = resolve(config, path).read_bytes()
    text = raw[len(BOM):] if raw.startswith(BOM) else raw
    return {
        "path": PurePosixPath(path.replace("\\", "/")).as_posix(),
        "text": text.decode("utf-8").replace("\r\n", "\n"),
        "sha": sha256(raw).hexdigest(),
        "bom": raw.startswith(BOM),
        "eol": "crlf" if b"\r\n" in raw else "lf",
    }


def encode(text: str, bom: bool, eol: str) -> bytes:
    body = text.replace("\r\n", "\n")
    if eol == "crlf":
        body = body.replace("\n", "\r\n")
    return (BOM if bom else b"") + body.encode("utf-8")


def check_plan(config: dict[str, Any], text: str) -> dict[str, Any]:
    """The plan as the stand reads it; a mistake is reported in words before anything is written."""
    try:
        plan = json.loads(text)
    except json.JSONDecodeError as error:
        raise StandError(422, f"План не є коректним JSON: рядок {error.lineno}, {error.msg}") from error
    if not isinstance(plan, dict) or not isinstance(plan.get("stages"), list):
        raise StandError(422, "У плані немає списку етапів (stages)")
    scripts = stand_dir() / config.get("scripts", "")
    seen: set[str] = set()
    for stage in plan["stages"]:
        if not isinstance(stage, dict) or not isinstance(stage.get("steps"), list):
            raise StandError(422, "Етап плану без списку кроків (steps)")
        for step in stage["steps"]:
            name = step.get("id") if isinstance(step, dict) else None
            if not name or not isinstance(name, str):
                raise StandError(422, f"Крок без id в етапі «{stage.get('title') or stage.get('id')}»")
            if stage.get("fixed"):
                continue
            if name in seen:
                raise StandError(422, f"Крок «{name}» повторюється: id має бути унікальним")
            seen.add(name)
            script = step.get("script")
            if not script or not (scripts / script).is_file():
                raise StandError(422, f"Крок «{name}»: скрипт не знайдено: {script}")
            if not isinstance(step.get("args", []), list) or not all(isinstance(item, str) for item in step.get("args", [])):
                raise StandError(422, f"Крок «{name}»: args має бути списком рядків")
            scope = step.get("scope")
            if scope not in (None, "case", "suite") and not (isinstance(scope, str) and scope.startswith("suite-")):
                raise StandError(422, f"Крок «{name}»: scope має бути case, suite або suite-<змінна плану>")
            if step.get("equip") not in EQUIP:
                raise StandError(422, f"Крок «{name}»: equip має бути ensure або auto")
            if not isinstance(step.get("when", {}), dict):
                raise StandError(422, f"Крок «{name}»: when має бути об'єктом умов")
    return plan


def parse_prep_line(line: str) -> dict[str, Any] | None:
    match = PREP_LINE.search(line)
    if not match:
        return None
    state, number, total, step, ms, changed, title = match.groups()
    return {"state": state, "n": int(number), "total": int(total), "step": step,
            "ms": int(ms) if ms else None, "changed": changed or None, "title": title.strip()}


def step_stats(timelines: list[list[dict[str, Any]]]) -> dict[str, dict[str, Any]]:
    """Per step: mean time of its recent successful runs and how it ended last time (newest timeline first)."""
    stats: dict[str, dict[str, Any]] = {}
    for timeline in timelines:
        for item in timeline:
            prep = item.get("prep")
            if not prep or prep.get("state") == "start":
                continue
            entry = stats.setdefault(prep["step"], {"times": [], "last": None})
            if entry["last"] is None:
                entry["last"] = {"state": prep["state"], "changed": prep.get("changed"), "ms": prep.get("ms"), "t": item.get("t")}
            if prep["state"] == "ok" and prep.get("ms") is not None and len(entry["times"]) < 10:
                entry["times"].append(prep["ms"])
    return {step: {"mean_ms": round(sum(value["times"]) / len(value["times"])) if value["times"] else None,
                   "runs": len(value["times"]), "last": value["last"]} for step, value in stats.items()}
