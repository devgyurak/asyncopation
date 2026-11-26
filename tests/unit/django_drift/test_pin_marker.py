"""The ``django_pin`` marker is what the django-main canary deselects (issue #47)."""

import pytest

from tests.support.pin_marker import (
    ASGIREF_VERSION_TEST,
    DRIFT_DIR,
    PIN_TESTS,
    canary_drift_command,
    collected_test_names,
    drift_step_requirements,
    runs_drift_folder,
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


def test_the_canary_runs_the_drift_tests_without_resyncing():
    # when
    command = canary_drift_command()
    # then
    assert "uv run --no-sync" in command


def test_the_canary_runs_the_drift_tests_without_the_pin_tests():
    # when
    command = canary_drift_command()
    # then
    assert '-m "not django_pin"' in command


def test_the_canary_also_runs_the_compile_unit_tests():
    # when
    command = canary_drift_command()
    # then
    assert "tests/unit/compile" in command


@pytest.mark.parametrize("drift_steps", [0, 2])
def test_the_canary_lookup_rejects_a_workflow_without_exactly_one_drift_step(tmp_path, drift_steps):
    # given
    workflow = write_workflow(tmp_path / "ci.yml", drift_steps)
    # when / then
    with pytest.raises(RuntimeError, match=f"found {drift_steps}$"):
        canary_drift_command(workflow)


def test_the_canary_lookup_returns_the_single_drift_step_and_ignores_comments(tmp_path):
    # given
    comment = f"      # run: uv run pytest {DRIFT_DIR}\n      - run: make lint\n"
    # when
    command = canary_drift_command(write_workflow(tmp_path / "ci.yml", 1, comment))
    # then
    assert command == f'uv run --no-sync pytest {DRIFT_DIR} -m "not django_pin"'


def test_the_canary_lookup_ignores_a_drift_step_of_another_job(tmp_path):
    # given
    workflow = write_workflow(tmp_path / "ci.yml", 1, other_job_drift_steps=2)
    # when
    command = canary_drift_command(workflow)
    # then
    assert command == f'uv run --no-sync pytest {DRIFT_DIR} -m "not django_pin"'


def test_the_canary_lookup_finds_nothing_when_only_another_job_runs_the_drift_suite(tmp_path):
    # given
    workflow = write_workflow(tmp_path / "ci.yml", 0, other_job_drift_steps=1)
    # when / then
    with pytest.raises(RuntimeError, match=r"found 0$"):
        canary_drift_command(workflow)


def test_the_canary_lookup_reads_a_step_with_an_env_block(tmp_path):
    # given
    extra = (
        '      - env:\n          ASYNCOPATION_REQUIRE_MYSQLDB: "1"\n'
        f'        run: uv run --no-sync pytest {DRIFT_DIR} tests/unit/compile -m "not django_pin"\n'
    )
    # when
    command = canary_drift_command(write_workflow(tmp_path / "ci.yml", 0, extra))
    # then
    assert command.endswith('tests/unit/compile -m "not django_pin"')


def test_the_canary_lookup_does_not_take_a_longer_folder_name_for_the_drift_folder(tmp_path):
    # given
    extra = f"      - run: uv run pytest {DRIFT_DIR}_x\n"
    # when
    command = canary_drift_command(write_workflow(tmp_path / "ci.yml", 1, extra))
    # then
    assert command == f'uv run --no-sync pytest {DRIFT_DIR} -m "not django_pin"'


@pytest.mark.parametrize(
    "text",
    ["", "jobs:\n", "jobs: []\n", "jobs:\n  django-main: 3\n", "- a\n"],
    ids=["empty", "no-job", "jobs-list", "job-scalar", "top-level-list"],
)
def test_the_canary_lookup_rejects_a_workflow_without_a_django_main_mapping(tmp_path, text):
    # given
    workflow = tmp_path / "ci.yml"
    workflow.write_text(text, encoding="utf-8")
    # when / then
    with pytest.raises(RuntimeError, match=r"no mapping at jobs\.django-main"):
        canary_drift_command(workflow)


def test_every_ci_step_running_the_mysql_drift_tests_requires_mysqldb():
    # when
    requirements = drift_step_requirements()
    # then
    assert requirements == {"integration": ["1"], "django-main": ["1"]}


def test_the_requirement_lookup_reports_a_drift_step_without_the_variable_as_none(tmp_path):
    # when
    requirements = drift_step_requirements(write_workflow(tmp_path / "ci.yml", 1))
    # then
    assert requirements == {"django-main": [None]}


@pytest.mark.parametrize(
    "run",
    [
        f"uv run pytest -q {DRIFT_DIR}",
        f'uv run --no-sync pytest -q -x {DRIFT_DIR}/ -m "not django_pin"',
        f"uv run pytest -p no:cacheprovider {DRIFT_DIR} tests/unit/compile",
        f"uv run pytest ./{DRIFT_DIR}",
        f"uv run pytest -k 'x {DRIFT_DIR}",
        f"echo it's\nuv run pytest {DRIFT_DIR}",
        f"make x && uv run pytest -q {DRIFT_DIR} -m 'not django_pin'",
        f"uv run pytest -k a#b {DRIFT_DIR}",
        f"uv run pytest -k 'a#b {DRIFT_DIR}",
        f"uv run pytest -k 'a #b' {DRIFT_DIR}",
        f'uv run pytest -m "x #y" {DRIFT_DIR}',
        f'uv run pytest -k "a \\" #b" {DRIFT_DIR}',
        f"uv run pytest -q &> log {DRIFT_DIR} it's",
        f"uv run pytest -q \\\n  {DRIFT_DIR} # note",
        f"# c \\\nuv run pytest {DRIFT_DIR}",
        f"uv run pytest {DRIFT_DIR} it's",
        f"uv run pytest -q 2>&1 {DRIFT_DIR} it's",
        f"uv run pytest -q&&ls x; uv run pytest {DRIFT_DIR} it's",
        f"uv run pytest {DRIFT_DIR} \\",
        f"uv run pytest -q 2>&1 {DRIFT_DIR}",
        f"uv run pytest -q > out.txt {DRIFT_DIR}",
        f"uv run pytest -q &> out.txt {DRIFT_DIR}",
    ],
    ids=[
        "option-before-path",
        "trailing-slash",
        "path-before-other-path",
        "dot-slash",
        "unbalanced-quote",
        "unbalanced-quote-earlier-line",
        "after-and",
        "mid-word-hash",
        "mid-word-hash-unbalanced-quote",
        "quoted-hash-single",
        "quoted-hash-double",
        "escaped-quote-in-double-quotes",
        "redirect-both-in-segment",
        "continued-line-with-comment",
        "comment-ending-in-backslash",
        "fallback-plain",
        "stderr-redirect-fallback",
        "unspaced-and-then-pytest",
        "backslash-at-the-end-of-the-command",
        "stderr-redirect",
        "redirect-with-the-folder-after-it",
        "redirect-both-with-the-folder-after-it",
    ],
)
def test_a_command_running_pytest_on_the_drift_folder_is_a_drift_step(run):
    # when
    matched = runs_drift_folder(run)
    # then
    assert matched is True


def test_a_backslash_continued_command_is_a_drift_step():
    # when
    matched = runs_drift_folder(f"uv run pytest -q \\\n  {DRIFT_DIR} -m 'not django_pin'")
    # then
    assert matched is True


@pytest.mark.parametrize(
    "run",
    [
        f"echo pytest {DRIFT_DIR}",
        f"cat <<EOF\nuv run pytest {DRIFT_DIR}\nEOF",
        f'python -c "x = 1\nuv run pytest {DRIFT_DIR} "',
    ],
    ids=[
        "known-limit-any-pytest-token-counts",
        "known-limit-heredoc-text-is-read-as-commands",
        "known-limit-multi-line-quoted-string-is-read-as-commands",
    ],
)
def test_the_known_limits_of_the_lookup_read_the_command_as_a_drift_step(run):
    # when
    matched = runs_drift_folder(run)
    # then
    assert matched is True


@pytest.mark.parametrize(
    "run",
    [
        f"uv run pytest {DRIFT_DIR}_x",
        "uv run pytest tests/unit",
        f"echo {DRIFT_DIR}",
        f"uv run pytest ./{DRIFT_DIR}_x it's",
        f"uv run pytest ../{DRIFT_DIR}",
        f"uv run pytest /abs/{DRIFT_DIR}",
    ],
    ids=[
        "longer-name",
        "other-folder",
        "not-pytest",
        "dot-slash-longer-name",
        "parent-path",
        "absolute-path",
    ],
)
def test_a_command_with_another_path_than_the_drift_folder_is_not_a_drift_step(run):
    # when
    matched = runs_drift_folder(run)
    # then
    assert matched is False


@pytest.mark.parametrize(
    "run",
    [
        f"# pytest {DRIFT_DIR}",
        f"echo it's # pytest {DRIFT_DIR}",
        f"echo it's\n# pytest {DRIFT_DIR}",
        f"uv run pytest -k a#b {DRIFT_DIR}_x",
        f"uv run pytest -k 'a #b' {DRIFT_DIR}_x",
        f"uv run pytest -k 'a\\' #b {DRIFT_DIR}",
        f"uv run pytest -k 'a\\' #b' {DRIFT_DIR}",
        f"uv run pytest -k 'a #b' {DRIFT_DIR} it's",
    ],
    ids=[
        "comment",
        "comment-after-unbalanced-quote",
        "comment-line-after-unbalanced-quote",
        "mid-word-hash",
        "quoted-hash-longer-name",
        "backslash-in-single-quotes",
        "backslash-in-single-quotes-closing-quote-later",
        "known-limit-quoted-hash-after-unbalanced-quote",
    ],
)
def test_a_command_with_the_folder_in_a_comment_or_quoted_text_is_not_a_drift_step(run):
    # when
    matched = runs_drift_folder(run)
    # then
    assert matched is False


@pytest.mark.parametrize(
    "run",
    [
        f"uv run pytest -q && ls {DRIFT_DIR}",
        f"ls {DRIFT_DIR}; uv run pytest -q",
        f"uv run pytest -q | tee {DRIFT_DIR}",
        f"uv run pytest -q && ls {DRIFT_DIR} it's",
        f"uv run pytest -q;ls {DRIFT_DIR} it's",
        f"uv run pytest -q || ls {DRIFT_DIR}",
        f"uv run pytest -q & ls {DRIFT_DIR}",
        f"uv run pytest -q&&ls {DRIFT_DIR} it's",
        f"uv run pytest -q|ls {DRIFT_DIR} it's",
        f"uv run pytest -q||ls {DRIFT_DIR} it's",
        f"uv run pytest -q&ls {DRIFT_DIR} it's",
        f"uv run pytest -q &> log; ls {DRIFT_DIR} it's",
        f"uv run pytest -q\nls {DRIFT_DIR}",
        f"uv run pytest -q |& ls {DRIFT_DIR}",
        f"uv run pytest -q |&ls {DRIFT_DIR} it's",
        f"uv run pytest -q > {DRIFT_DIR}",
        f"uv run pytest -q >> {DRIFT_DIR}",
        f"uv run pytest -q 2> {DRIFT_DIR}",
        f"uv run pytest -q &> {DRIFT_DIR}",
        f"uv run pytest -q < {DRIFT_DIR}",
        f"uv run pytest -q 2>&1 && ls {DRIFT_DIR}",
        f"uv run pytest -q 2> {DRIFT_DIR} it's",
    ],
    ids=[
        "other-segment-after-and",
        "other-segment-before-semicolon",
        "other-segment-pipe",
        "other-segment-unbalanced-quote",
        "other-segment-unspaced-separator",
        "other-segment-or",
        "other-segment-background",
        "unspaced-and-unbalanced-quote",
        "unspaced-pipe-unbalanced-quote",
        "unspaced-or-unbalanced-quote",
        "unspaced-background-unbalanced-quote",
        "redirect-both-then-other-segment",
        "newline-separates-commands",
        "pipe-both",
        "unspaced-pipe-both-unbalanced-quote",
        "redirect-target",
        "append-target",
        "stderr-target",
        "redirect-both-target",
        "input-redirect",
        "stderr-redirect-then-other-segment",
        "stderr-target-unbalanced-quote",
    ],
)
def test_a_command_with_the_folder_in_another_segment_or_a_redirection_is_not_a_drift_step(run):
    # when
    matched = runs_drift_folder(run)
    # then
    assert matched is False


@pytest.mark.parametrize(
    "run",
    [
        f"uv run pytest -q \\\n&& ls {DRIFT_DIR}",
        f"uv run pytest -q # c \\\nls {DRIFT_DIR}",
        f"uv run pytest -q \\\\\nls {DRIFT_DIR}",
        f"uv run pytest -q\\\n{DRIFT_DIR}",
        f"uv run pytest {DRIFT_DIR}\\\n#x",
        f"uv run pytest -q \\\n#x {DRIFT_DIR}",
    ],
    ids=[
        "continuation-before-separator",
        "comment-ending-in-backslash-then-other-command",
        "escaped-backslash-does-not-continue",
        "continuation-joins-without-a-space",
        "continuation-then-hash-joins-the-word",
        "continuation-then-hash-word-is-a-comment",
    ],
)
def test_a_command_with_the_folder_after_a_continuation_that_changes_it_is_not_a_drift_step(run):
    # when
    matched = runs_drift_folder(run)
    # then
    assert matched is False


def test_the_canary_lookup_finds_a_drift_step_with_options(tmp_path):
    # given
    extra = f"      - run: uv run --no-sync pytest -q {DRIFT_DIR}\n"
    # when
    command = canary_drift_command(write_workflow(tmp_path / "ci.yml", 0, extra))
    # then
    assert command == f"uv run --no-sync pytest -q {DRIFT_DIR}"


def test_the_requirement_lookup_finds_a_drift_step_with_options(tmp_path):
    # given
    extra = f"      - run: uv run pytest -q {DRIFT_DIR}\n"
    # when
    requirements = drift_step_requirements(write_workflow(tmp_path / "ci.yml", 0, extra))
    # then
    assert requirements == {"django-main": [None]}


def test_the_requirement_lookup_fails_loudly_when_no_step_runs_the_drift_folder(tmp_path):
    # given
    workflow = write_workflow(tmp_path / "ci.yml", 0)
    # when / then
    with pytest.raises(RuntimeError, match=r"no step of ci\.yml runs the drift folder"):
        drift_step_requirements(workflow)
