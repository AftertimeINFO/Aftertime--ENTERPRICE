"""Upload ``allure-results`` directories into Aftertime reports (read-only on disk)."""

from __future__ import annotations

import json
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Any, Callable


def read_launch(folder: Path) -> dict[str, Any]:
    results = []
    for path in sorted(folder.glob("*-result.json")):
        try:
            results.append(json.loads(path.read_text(encoding="utf-8-sig")))
        except (OSError, json.JSONDecodeError):
            continue
    payload: dict[str, Any] = {"name": folder.name, "results": results}
    for name, key in (("categories.json", "categories"), ("executor.json", "executor")):
        path = folder / name
        if path.exists():
            try:
                payload[key] = json.loads(path.read_text(encoding="utf-8-sig"))
            except json.JSONDecodeError:
                pass
    properties = folder / "environment.properties"
    if properties.exists():
        payload["environment"] = dict(
            line.split("=", 1) for line in properties.read_text(encoding="utf-8-sig").splitlines()
            if "=" in line and not line.startswith("#")
        )
    return payload


def launch_folders(root: Path) -> list[Path]:
    """Launch directories under a results root; merged views (``_current``…) are skipped."""
    return sorted(
        path for path in root.iterdir()
        if path.is_dir() and not path.name.startswith(("_", ".")) and any(path.glob("*-result.json"))
    )


class Uploader:
    def __init__(self, api_url: str, timeout: float = 120) -> None:
        self.api_url = api_url.rstrip("/")
        self.timeout = timeout

    def _send(self, method: str, path: str, data: bytes, content_type: str) -> Any:
        request = urllib.request.Request(f"{self.api_url}{path}", method=method, data=data,
                                         headers={"Content-Type": content_type})
        with urllib.request.urlopen(request, timeout=self.timeout) as response:
            return json.load(response)

    def upload(self, folder: Path, e2e_run_id: str | None = None) -> dict[str, Any]:
        payload = read_launch(folder)
        if e2e_run_id:
            payload["e2e_run_id"] = e2e_run_id
        reply = self._send("POST", "/lab/reports/launches",
                           json.dumps(payload, ensure_ascii=False).encode("utf-8"), "application/json")
        uploaded = 0
        for source in reply.get("attachments_needed", []):
            path = folder / source
            if path.is_file():
                self._send("PUT", f"/lab/reports/attachments/{source}", path.read_bytes(), "application/octet-stream")
                uploaded += 1
        return {"folder": folder.name, "results": len(payload["results"]), "created": reply.get("created"),
                "attachments": uploaded, "launch_id": reply.get("launch_id")}


def newest_launch_for(root: Path, cases: list[str], since: datetime) -> Path | None:
    """The newest launch written after ``since`` that contains one of the cases."""
    candidates = [path for path in launch_folders(root) if datetime.fromtimestamp(path.stat().st_mtime) >= since]
    for folder in sorted(candidates, key=lambda path: path.stat().st_mtime, reverse=True):
        for path in folder.glob("*-result.json"):
            try:
                if json.loads(path.read_text(encoding="utf-8-sig")).get("fullName") in cases:
                    return folder
            except (OSError, json.JSONDecodeError):
                continue
    return None


def sync(root: Path, uploader: Uploader, echo: Callable[[dict], None]) -> int:
    count = 0
    for folder in launch_folders(root):
        try:
            echo({"type": "imported", **uploader.upload(folder)})
            count += 1
        except OSError as exc:
            echo({"type": "error", "folder": folder.name, "message": str(exc)})
    return count
