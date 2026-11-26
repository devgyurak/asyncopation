"""Positive controls for `refusal_of` (tests/support/manager_models.py): its detectors do report a
coroutine dropped unclosed and a thread started, so an empty report in the refusal tests means
something."""

from tests.support.manager_models import (
    leak_a_coroutine_and_refuse,
    refusal_of,
    start_a_thread_and_refuse,
)


def test_refusal_of_reports_a_coroutine_dropped_unclosed():
    # when
    refusal = refusal_of(leak_a_coroutine_and_refuse, "_leaked_coroutine")
    # then
    assert refusal.message == "refused after leaking a coroutine"
    assert len(refusal.unawaited) == 1
    assert "_leaked_coroutine" in refusal.unawaited[0]


def test_refusal_of_ignores_unawaited_warnings_of_other_coroutines():
    # when
    refusal = refusal_of(leak_a_coroutine_and_refuse, "another_name")
    # then
    assert refusal.unawaited == []


def test_refusal_of_counts_a_started_thread():
    # when
    refusal = refusal_of(start_a_thread_and_refuse, "none")
    # then
    assert refusal.message == "refused after starting a thread"
    assert refusal.thread_starts == 1
