"""The README examples must run as written."""

from tests.support.readme import run_snippets, section_snippets

SECTION = "Testing upper layers without a database"


def test_recording_driver_section_has_a_python_snippet():
    # when
    snippets = section_snippets(SECTION)
    # then
    assert len(snippets) >= 1


def test_recording_driver_snippets_run_as_written():
    # given
    snippets = section_snippets(SECTION)
    # when
    result = run_snippets(snippets)
    # then
    assert result.error is None
