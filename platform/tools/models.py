"""Export BPMN processes with their SVG copies and check that the pairs agree.

    python tools/models.py export [--api URL] [--out DIR] [--all-statuses]
    python tools/models.py check  [--dir DIR]

The default directory is ``$AFTERTIME_CONFIG_DIR/models`` (``../Aftertime-config``
next to the repository root when the variable is not set).  ``check`` exits
with code 1 when any SVG is missing, stale or incomplete.  Standard library only.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.services.bpmn_svg import check_svg  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[2]


def default_models_dir() -> Path:
    config = os.environ.get("AFTERTIME_CONFIG_DIR")
    base = Path(config) if config else REPO_ROOT.parent / "Aftertime-config"
    return base / "models"


def _get(api: str, path: str) -> dict:
    with urllib.request.urlopen(f"{api.rstrip('/')}{path}", timeout=30) as response:
        return json.load(response)


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
    check_cmd = commands.add_parser("check")
    check_cmd.add_argument("--dir", type=Path, default=default_models_dir())
    args = parser.parse_args(argv)
    if args.command == "export":
        return export(args.api, args.out, args.all_statuses)
    return check(args.dir)


if __name__ == "__main__":
    sys.exit(main())
