"""One shared wrapper, several threads with their own loops (ADR-0008, Decision 3)."""

from tests.support.compile_scope import thread_race_mismatches


def test_threads_sharing_a_wrapper_never_read_another_threads_autocommit(alias):
    # when
    mismatches = thread_race_mismatches(alias)
    # then
    assert mismatches == 0
