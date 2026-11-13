import shutil
import time

import pytest

from e2e_bridge.center import CenterError
from e2e_bridge.local_center import LocalCenter

pytestmark = pytest.mark.skipif(not shutil.which("pwsh"), reason="needs PowerShell 7")


def make(tmp_path, body):
    script = tmp_path / "run-all.ps1"
    script.write_text(body, encoding="utf-8")
    return LocalCenter(script, tmp_path / "jobs")


def wait_done(center, job_id, timeout=60):
    end = time.time() + timeout
    while time.time() < end:
        job = center.job(job_id)
        if job["status"] in ("passed", "failed", "stopped"):
            return job
        time.sleep(0.2)
    raise AssertionError("job did not finish")


def test_missing_script(tmp_path):
    with pytest.raises(CenterError):
        LocalCenter(tmp_path / "nope.ps1")


def test_refuses_empty_run(tmp_path):
    center = make(tmp_path, "exit 0")
    with pytest.raises(CenterError):
        center.run([], "t")
    with pytest.raises(CenterError):
        center.run(["bad name;rm"], "t")


def test_passes_arguments_and_streams_lines(tmp_path):
    center = make(tmp_path, "param($Case,$Suite,[switch]$DryRun)\nWrite-Output \"case=$Case suite=$Suite dry=$DryRun\"\nexit 0")
    job_id = center.run(["a", "b"], "t", dry_run=True)
    job = wait_done(center, job_id)
    assert job["status"] == "passed" and job["exit"] == 0
    assert "case=a,b suite= dry=True" in job["lines"]
    assert job["lines"][-1].startswith("--- finished: passed")
    assert center.job(job_id, job["next"])["lines"] == []
    assert (tmp_path / "jobs" / f"{job_id}.log").read_text(encoding="utf-8").strip().endswith("case=a,b suite= dry=True")


def test_failed_exit_code_and_queue_order(tmp_path):
    center = make(tmp_path, "param($Suite)\nStart-Sleep 1\nif ($Suite -eq 'bad') { exit 3 }\nexit 0")
    first = center.run([], "one", suites=["bad"])
    second = center.run([], "two", suites=["ok"])
    state = center.state()
    assert state["running"] in (first, None) and second in state["queue"] + [state["running"]]
    assert wait_done(center, first)["status"] == "failed"
    assert wait_done(center, second)["status"] == "passed"
    assert center.busy() == {"running": None, "queue": []}


def test_stop_queued_job(tmp_path):
    center = make(tmp_path, "Start-Sleep 3\nexit 0")
    first = center.run([], "one", suites=["a"])
    second = center.run([], "two", suites=["b"])
    assert center.stop(second) is True
    assert center.job(second)["status"] == "stopped"
    center.stop(first)
    wait_done(center, first)
