"""The ``django_pin`` marker is what the django-main canary deselects (issue #47)."""

import pytest

from tests.support.pin_marker import (
    ASGIREF_VERSION_TEST,
    PIN_TESTS,
    canary_drift_command,
    collected_test_names,
    write_workflow,
)


def test_django_pin_marks_exactly_the_two_pin_tests():
    # when
    marked = collected_test_names("django_pin")
    # then
    assert marked == PIN_TESTS


def test_the_canary_selection_drops_the_pin_tests():
    # when
    selected = collected_test_names("not django_pin")
    # then
    assert selected.isdisjoint(PIN_TESTS)


def test_the_canary_selection_keeps_the_asgiref_version_check():
    # when
    selected = collected_test_names("not django_pin")
    # then
    assert ASGIREF_VERSION_TEST in selected


def test_the_default_selection_sees_the_pin_and_asgiref_tests():
    # when
    everything = collected_test_names("not integration")
    # then
    assert everything >= PIN_TESTS | {ASGIREF_VERSION_TEST}


def test_the_canary_runs_the_drift_tests_without_resyncing_and_without_the_pin_tests():
    # when
    command = canary_drift_command()
    # then
    assert "uv run --no-sync" in command
    assert '-m "not django_pin"' in command


@pytest.mark.parametrize("drift_steps", [0, 2])
def test_the_canary_lookup_rejects_a_workflow_without_exactly_one_drift_step(tmp_path, drift_steps):
    # given
    workflow = write_workflow(tmp_path / "ci.yml", drift_steps)
    # when / then
    with pytest.raises(RuntimeError, match=f"found {drift_steps}"):
        canary_drift_command(workflow)
