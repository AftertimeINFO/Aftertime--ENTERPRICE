import json
import os
import time
from datetime import datetime, timedelta

from e2e_bridge.results import Uploader, launch_folders, newest_launch_for, read_launch


def make_launch(root, name, full_name="case-a", attachment="a1-attachment.png"):
    folder = root / name
    folder.mkdir()
    (folder / "r1-result.json").write_text(json.dumps({
        "uuid": f"{name}-r1", "fullName": full_name, "status": "passed",
        "attachments": [{"name": "Скріншот", "type": "image/png", "source": attachment}],
    }), encoding="utf-8")
    (folder / attachment).write_bytes(b"PNG")
    (folder / "categories.json").write_text(json.dumps([{"name": "Дефект"}]), encoding="utf-8")
    (folder / "executor.json").write_text(json.dumps({"buildName": name}), encoding="utf-8")
    (folder / "environment.properties").write_text("Hall=11\nPlatform=8.3=x86\n", encoding="utf-8")
    return folder


def test_launch_is_read_with_its_metadata(tmp_path):
    payload = read_launch(make_launch(tmp_path, "20261103-120000"))
    assert payload["name"] == "20261103-120000"
    assert payload["results"][0]["fullName"] == "case-a"
    assert payload["categories"] == [{"name": "Дефект"}]
    assert payload["environment"] == {"Hall": "11", "Platform": "8.3=x86"}
    assert payload["executor"]["buildName"] == "20261103-120000"


def test_merged_views_and_empty_folders_are_not_launches(tmp_path):
    make_launch(tmp_path, "20261103-120000")
    make_launch(tmp_path, "_current")
    (tmp_path / "20261103-130000").mkdir()
    assert [path.name for path in launch_folders(tmp_path)] == ["20261103-120000"]


def test_newest_launch_of_a_case_after_a_run_started(tmp_path):
    old = make_launch(tmp_path, "20261103-100000")
    other = make_launch(tmp_path, "20261103-110000", full_name="case-b")
    mine = make_launch(tmp_path, "20261103-120000")
    past = time.time() - 3600
    os.utime(old, (past, past))
    since = datetime.now() - timedelta(minutes=5)
    assert newest_launch_for(tmp_path, ["case-a"], since) == mine
    assert newest_launch_for(tmp_path, ["case-b"], since) == other
    assert newest_launch_for(tmp_path, ["case-z"], since) is None


def test_upload_sends_the_launch_then_only_needed_attachments(tmp_path, monkeypatch):
    folder = make_launch(tmp_path, "20261103-120000")
    calls = []

    def send(self, method, path, data, content_type):
        calls.append((method, path, content_type))
        if path == "/lab/reports/launches":
            body = json.loads(data)
            assert body["e2e_run_id"] == "run-1"
            return {"launch_id": "L1", "created": True, "attachments_needed": ["a1-attachment.png", "gone-attachment.txt"]}
        return {"size": len(data)}

    monkeypatch.setattr(Uploader, "_send", send)
    result = Uploader("http://aftertime/api/v1").upload(folder, e2e_run_id="run-1")
    assert result == {"folder": "20261103-120000", "results": 1, "created": True, "attachments": 1, "launch_id": "L1"}
    assert calls == [
        ("POST", "/lab/reports/launches", "application/json"),
        ("PUT", "/lab/reports/attachments/a1-attachment.png", "application/octet-stream"),
    ]
