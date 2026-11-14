"""A run keeps the stand (configuration version) it was executed on; a version changed mid-run is marked."""

from datetime import UTC, datetime

from app.api.e2e import apply_event, merge_stand
from app.models import LabE2eRun

T0 = datetime(2026, 11, 14, 1, 0, tzinfo=UTC)
T1 = datetime(2026, 11, 14, 1, 30, tzinfo=UTC)


def test_first_reading_is_the_stand_of_the_run():
    stand = merge_stand(None, {"type": "stand", "version": "1.75.0.2", "source": "http://x/test/version"}, T0)
    assert stand == {"version": "1.75.0.2", "source": "http://x/test/version", "at": T0.isoformat()}


def test_same_version_at_the_end_is_not_a_change():
    start = merge_stand(None, {"version": "1.75.0.2"}, T0)
    stand = merge_stand(start, {"version": "1.75.0.2"}, T1)
    assert stand["version"] == "1.75.0.2" and stand["end"] == {"version": "1.75.0.2", "at": T1.isoformat()}
    assert stand["changed"] is False


def test_other_version_at_the_end_is_a_change():
    start = merge_stand(None, {"version": "1.75.0.2"}, T0)
    stand = merge_stand(start, {"version": "1.75.0.3"}, T1)
    assert stand["version"] == "1.75.0.2" and stand["end"]["version"] == "1.75.0.3" and stand["changed"] is True


def test_a_failed_reading_is_kept_and_empty_events_change_nothing():
    failed = merge_stand(None, {"error": "B24 недоступний"}, T0)
    assert failed == {"error": "B24 недоступний", "at": T0.isoformat()}
    assert merge_stand(failed, {"type": "stand"}, T1) is failed


def test_stand_event_is_applied_to_the_run():
    run = LabE2eRun(id="r", binding="", states=[], log=[], timeline=[])
    apply_event(run, {"type": "stand", "version": "1.75.0.2"})
    assert run.stand["version"] == "1.75.0.2"
