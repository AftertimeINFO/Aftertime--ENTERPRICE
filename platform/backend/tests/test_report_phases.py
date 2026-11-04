from app.services.report_phases import derive_states, timeline_of

ISSUE = {
    "phases": ["login", "order", "serials", "issue"],
    "steps": {"mode": "match", "rules": {"login": "SignIn", "order": "NewOrder", "serials": "ScanItem",
                                         "issue": "PrintWaybill"}},
}


def step(name, status="passed", steps=None, message=None, start=None):
    item = {"name": name, "status": status, "steps": steps or []}
    if message:
        item["statusDetails"] = {"message": message}
    if start:
        item["start"] = start
        item["stop"] = start + 1000
    return item


def states(result):
    return {item["phase"]: item["state"] for item in result}


def test_a_passed_result_passes_every_phase_even_without_markers():
    assert states(derive_states(ISSUE, "passed", {"steps": [step("Сценарій")]})) == dict.fromkeys(ISSUE["phases"], "passed")


def test_a_failing_step_fails_the_phase_after_the_last_one_proven():
    data = {"steps": [step("Сценарій", "failed", [
        step('I press the button "SignIn"'),
        step('I press the button "NewOrder"'),
        step("Тогда элемент формы стал равен", "failed", message="очікувалось 2"),
    ])]}
    result = derive_states(ISSUE, "failed", data)
    assert states(result) == {"login": "passed", "order": "passed", "serials": "failed", "issue": "not-reached"}
    assert result[2]["detail"] == "очікувалось 2"


def test_a_marker_step_that_failed_fails_its_own_phase():
    data = {"steps": [step('press "SignIn"'), step('press "ScanItem"', "broken", message="серії немає")]}
    result = derive_states(ISSUE, "broken", data)
    assert states(result) == {"login": "passed", "order": "passed", "serials": "failed", "issue": "not-reached"}
    assert result[2]["evidence"].startswith("press") and not result[2]["inferred"]


def test_without_steps_the_failure_message_places_the_failure():
    result = derive_states(ISSUE, "failed", {"steps": []}, "Крок ScanItem: серію не знайдено")
    assert states(result) == {"login": "passed", "order": "passed", "serials": "failed", "issue": "not-reached"}
    unplaced = derive_states(ISSUE, "failed", {"steps": []}, "таймаут")
    assert states(unplaced) == {"login": "failed", "order": "not-reached", "serials": "not-reached", "issue": "not-reached"}


def test_index_mode_reads_top_level_steps_in_order():
    binding = {"phases": ["register", "select", "pay"], "steps": {"mode": "index"}}
    data = {"steps": [step("Реєстрація"), step("Підбір", "failed")]}
    assert states(derive_states(binding, "failed", data)) == {"register": "passed", "select": "failed", "pay": "not-reached"}


def test_outcome_mode_and_skipped_results():
    binding = {"phases": ["a", "b"], "steps": {"mode": "outcome"}}
    assert states(derive_states(binding, "passed", {})) == {"a": "passed", "b": "passed"}
    assert states(derive_states(binding, "skipped", {})) == {"a": "stopped", "b": "not-reached"}


def test_the_timeline_keeps_only_step_times_inside_the_run():
    from datetime import UTC, datetime

    data = {"steps": [step('"SignIn"', start=1_800_000_000_000), step('"NewOrder"', start=1_700_000_000_000)]}
    result = derive_states(ISSUE, "passed", data)
    since = datetime.fromtimestamp(1_799_999_000, UTC)
    timeline = timeline_of(result, since, datetime.fromtimestamp(1_800_001_000, UTC))
    assert [entry["phase"] for entry in timeline] == ["login"]


def test_steps_a_failed_scenario_never_ran_prove_nothing():
    data = {"steps": [step('"SignIn"'), step("падіння", "failed"), step('"PrintWaybill"', "skipped")]}
    assert states(derive_states(ISSUE, "failed", data)) == {"login": "passed", "order": "failed", "serials": "not-reached",
                                                           "issue": "not-reached"}


def test_a_group_of_skipped_steps_did_not_run():
    data = {"steps": [step('"SignIn"'), step("Група", "failed", [step("падіння", "failed")]),
                      step("Видача", "passed", [step('"PrintWaybill"', "skipped")])]}
    assert states(derive_states(ISSUE, "failed", data))["issue"] == "not-reached"
