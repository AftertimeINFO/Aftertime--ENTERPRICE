"""Model packs: domain models plugged into the Aftertime core.

A pack is a directory (usually its own repository) with ``aftertime-pack.json``::

    {
      "id": "rental",
      "title": "Прокат спорядження",
      "version": "1.0.0",
      "core": ">=0.4",
      "description": "...",
      "processes": "processes/*.bpmn",      # BPMN processes, imported and published
      "bindings": "e2e/bindings.json",       # processes ↔ E2E cases (optional)
      "installer": "pack_install.py"         # optional: install(db, pack) for system models
    }

Packs are discovered in ``$AFTERTIME_PACKS_DIR`` (each sub-directory with a manifest).
Installing is idempotent: a BPMN document already stored is not imported again.
"""

from __future__ import annotations

import importlib.util
import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from xml.etree import ElementTree

MANIFEST = "aftertime-pack.json"
BPMN_NS = "http://www.omg.org/spec/BPMN/20100524/MODEL"


@dataclass
class Pack:
    id: str
    title: str
    version: str
    path: Path
    description: str = ""
    processes: str = ""
    bindings: str = ""
    installer: str = ""
    manifest: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def load(cls, path: Path) -> "Pack":
        data = json.loads((path / MANIFEST).read_text(encoding="utf-8"))
        if not data.get("id"):
            raise ValueError(f"{path / MANIFEST}: немає id")
        return cls(
            id=str(data["id"]),
            title=str(data.get("title") or data["id"]),
            version=str(data.get("version") or "0"),
            path=path,
            description=str(data.get("description") or ""),
            processes=str(data.get("processes") or ""),
            bindings=str(data.get("bindings") or ""),
            installer=str(data.get("installer") or ""),
            manifest=data,
        )

    def process_files(self) -> list[Path]:
        return sorted(self.path.glob(self.processes)) if self.processes else []

    def bindings_data(self) -> dict[str, dict[str, Any]]:
        file = self.path / self.bindings if self.bindings else None
        if not file or not file.exists():
            return {}
        data = json.loads(file.read_text(encoding="utf-8"))
        return {key: {**value, "pack": self.id} for key, value in data.items() if isinstance(value, dict)}

    def summary(self) -> dict[str, Any]:
        return {"id": self.id, "title": self.title, "version": self.version, "description": self.description,
                "processes": [file.name for file in self.process_files()],
                "bindings": sorted(self.bindings_data()), "installer": bool(self.installer)}


def packs_dir() -> Path:
    return Path(os.environ.get("AFTERTIME_PACKS_DIR", "/packs"))


def discover(root: Path | None = None) -> dict[str, Pack]:
    """Packs found under the packs directory; broken manifests are skipped, not fatal."""
    root = root or packs_dir()
    found: dict[str, Pack] = {}
    if not root.is_dir():
        return found
    candidates = [root] if (root / MANIFEST).exists() else sorted(path for path in root.iterdir() if path.is_dir())
    for path in candidates:
        if (path / MANIFEST).exists():
            try:
                pack = Pack.load(path)
            except (OSError, ValueError, json.JSONDecodeError):
                continue
            found.setdefault(pack.id, pack)
    return found


def process_identity(bpmn_xml: str) -> tuple[str, str]:
    process = ElementTree.fromstring(bpmn_xml).find(f"{{{BPMN_NS}}}process")
    if process is None or not process.get("id"):
        raise ValueError("BPMN-документ не має bpmn:process з id")
    return process.get("id"), process.get("name") or process.get("id")


def run_installer(pack: Pack, db: Any) -> Any:
    """Call ``install(db, pack)`` of a pack that ships code (system models, scenarios)."""
    if not pack.installer:
        return None
    file = pack.path / pack.installer
    spec = importlib.util.spec_from_file_location(f"aftertime_pack_{pack.id}", file)
    if spec is None or spec.loader is None:
        raise ValueError(f"{file}: не вдалося завантажити інсталятор")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.install(db, pack)
