"""Export BPMN processes with their SVG copies and check that the pairs agree.

    python tools/models.py export [--api URL] [--out DIR] [--all-statuses]
    python tools/models.py check  [--dir DIR]
    python tools/models.py import FILE.bpmn [--api URL] [--publish]

The default directory is ``$AFTERTIME_CONFIG_DIR/models`` (``../Aftertime-config``
next to the repository root when the variable is not set).  ``check`` exits
with code 1 when any SVG is missing, stale or incomplete.  Standard library only.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path
from xml.etree import ElementTree

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.services.bpmn_svg import BPMN_NS, bpmn_sha256, check_svg  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[2]


def default_models_dir() -> Path:
    config = os.environ.get("AFTERTIME_CONFIG_DIR")
    base = Path(config) if config else REPO_ROOT.parent / "Aftertime-config"
    return base / "models"


def _get(api: str, path: str) -> dict:
    with urllib.request.urlopen(f"{api.rstrip('/')}{path}", timeout=30) as response:
        return json.load(response)


def _send(api: str, method: str, path: str, body: dict | None = None) -> dict:
    request = urllib.request.Request(
        f"{api.rstrip('/')}{path}",
        method=method,
        data=json.dumps(body).encode("utf-8") if body is not None else None,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            return json.load(response)
    except urllib.error.HTTPError as exc:
        raise SystemExit(f"{method} {path}: {exc.code} {exc.read().decode('utf-8', 'replace')}") from exc


def latest_versions(workflows: list[dict], all_statuses: bool) -> list[dict]:
    """Newest version of each workflow; published only unless asked otherwise."""
    latest: dict[str, dict] = {}
    for version in workflows:
        if not all_statuses and version.get("status") != "published":
            continue
        current = latest.get(version["workflow_id"])
        if current is None or version["version"] > current["version"]:
            latest[version["workflow_id"]] = version
    return sorted(latest.values(), key=lambda item: item["workflow_id"])


def export(api: str, out: Path, all_statuses: bool) -> int:
    out.mkdir(parents=True, exist_ok=True)
    workflows = _get(api, "/lab/workspace").get("workflows", [])
    index = []
    for version in latest_versions(workflows, all_statuses):
        item = _get(api, f"/lab/workflows/{version['id']}/export")
        stem = item["workflow_id"]
        (out / f"{stem}.bpmn").write_text(item["bpmn_xml"], encoding="utf-8", newline="")
        if item["bpmn_svg"]:
            (out / f"{stem}.svg").write_text(item["bpmn_svg"], encoding="utf-8", newline="")
        index.append({
            "workflow_id": stem,
            "name": item["name"],
            "version": item["version"],
            "status": item["status"],
            "bpmn_sha256": item["bpmn_sha256"],
            "svg": item["presentation"]["status"],
        })
        print(f"{stem}: v{item['version']} {item['status']}, SVG {item['presentation']['status']}")
    (out / "index.json").write_text(
        json.dumps(index, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline=""
    )
    return 0


def import_model(api: str, path: Path, publish: bool) -> int:
    """Create or update the Model Lab process whose id is the BPMN process id."""
    xml = path.read_text(encoding="utf-8")
    process = ElementTree.fromstring(xml).find(f"{{{BPMN_NS}}}process")
    if process is None or not process.get("id"):
        raise SystemExit(f"{path.name}: немає bpmn:process з id")
    workflow_id, name = process.get("id"), process.get("name") or process.get("id")
    versions = [item for item in _get(api, "/lab/workspace").get("workflows", []) if item["workflow_id"] == workflow_id]
    latest = max(versions, key=lambda item: item["version"]) if versions else None
    if latest and bpmn_sha256(latest["bpmn_xml"]) == bpmn_sha256(xml) and (latest["status"] == "published" or not publish):
        print(f"{workflow_id}: v{latest['version']} {latest['status']} уже містить цей BPMN")
        return 0
    if latest is None:
        draft = _send(api, "POST", "/lab/workflows", {"id": workflow_id, "name": name})["version"]
    elif latest["status"] == "draft":
        draft = latest
    else:
        draft = _send(api, "POST", f"/lab/workflows/{latest['id']}/clone")["version"]
    saved = _send(api, "PUT", f"/lab/workflows/{draft['id']}/bpmn", {"bpmn_xml": xml})
    print(f"{workflow_id}: BPMN збережено у v{draft['version']} (draft)")
    test = saved.get("test")
    if test:
        for item in test["phases"]:
            print(f"  {'✓' if item['element'] else '✗'} {item['phase']} {item['element'] or ''}")
        for message in test["errors"] + test["warnings"]:
            print(f"  ! {message}")
    if publish:
        report = _send(api, "POST", f"/lab/workflows/{draft['id']}/validate")
        if not report["valid"]:
            print("  не опубліковано: " + "; ".join(report["errors"]))
            return 1
        _send(api, "POST", f"/lab/workflows/{draft['id']}/publish")
        print(f"{workflow_id}: v{draft['version']} опубліковано")
    print("SVG з'явиться після першого відкриття процесу в редакторі Model Lab.")
    return 0


def check(directory: Path) -> int:
    failures = 0
    models = sorted(directory.glob("*.bpmn"))
    for bpmn in models:
        svg = bpmn.with_suffix(".svg")
        if not svg.exists():
            print(f"FAIL {bpmn.name}: немає {svg.name}")
            failures += 1
            continue
        report = check_svg(
            svg.read_text(encoding="utf-8"), bpmn.read_text(encoding="utf-8")
        )
        if report["status"] != "synced":
            missing = f" (немає: {', '.join(report['missing'])})" if report["missing"] else ""
            print(f"FAIL {bpmn.name}: SVG {report['status']}{missing}")
            failures += 1
        else:
            print(f"OK   {bpmn.name}: {report['elements']} елементів")
    print(f"{len(models) - failures}/{len(models)} моделей синхронізовано")
    return 1 if failures else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    commands = parser.add_subparsers(dest="command", required=True)
    export_cmd = commands.add_parser("export")
    export_cmd.add_argument("--api", default=os.environ.get("AFTERTIME_API_URL", "http://localhost:8000/api/v1"))
    export_cmd.add_argument("--out", type=Path, default=default_models_dir())
    export_cmd.add_argument("--all-statuses", action="store_true", help="включити чернетки")
    import_cmd = commands.add_parser("import")
    import_cmd.add_argument("file", type=Path)
    import_cmd.add_argument("--api", default=os.environ.get("AFTERTIME_API_URL", "http://localhost:8000/api/v1"))
    import_cmd.add_argument("--publish", action="store_true")
    check_cmd = commands.add_parser("check")
    check_cmd.add_argument("--dir", type=Path, default=default_models_dir())
    args = parser.parse_args(argv)
    if args.command == "export":
        return export(args.api, args.out, args.all_statuses)
    if args.command == "import":
        return import_model(args.api, args.file, args.publish)
    return check(args.dir)


if __name__ == "__main__":
    sys.exit(main())
