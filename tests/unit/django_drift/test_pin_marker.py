"""The ``django_pin`` marker is what the django-main canary deselects (issue #47).

Also pins the CI drift steps; the canary's drift step must follow the Django main install step
directly, on purpose.
"""

import pytest

from tests.support.pin_marker import (
    ASGIREF_VERSION_TEST,
    CANARY_INSTALL_RUN,
    CANARY_STEP,
    CI_WORKFLOW,
    DRIFT_DIR,
    INTEGRATION_STEP,
    PIN_TESTS,
    collected_test_names,
    drift_step,
    drift_step_problems,
    find_step,
    is_single_problem,
    write_workflow,
)

CANARY = ("django-main", "django-drift")
DRIFT = {"integration": ("integration", "integration-drift"), "django-main": CANARY}
INTEGRATION = ("integration", "integration-drift")
CANARY_RUN = f'uv run --no-sync pytest {DRIFT_DIR} tests/unit/compile -m "not django_pin"'


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


def test_the_ci_drift_steps_are_the_expected_ones():
    # when
    problems = drift_step_problems()
    # then
    assert problems == []


@pytest.mark.parametrize(
    ("step", "expected"),
    [(CANARY, CANARY_STEP), (INTEGRATION, INTEGRATION_STEP)],
    ids=["canary", "integration"],
)
def test_a_drift_step_is_found_by_its_id_in_ci_without_its_name(step, expected):
    # when
    found = find_step(CI_WORKFLOW, *step)
    # then
    assert found | {"name": None} == expected | {"name": None}


def test_a_workflow_with_the_expected_drift_steps_has_no_problems(tmp_path):
    # when
    problems = drift_step_problems(write_workflow(tmp_path / "ci.yml"))
    # then
    assert problems == []


@pytest.mark.parametrize(
    "run",
    [
        f'uv run --no-sync true && uv run pytest {DRIFT_DIR} tests/unit/compile -m "not django_pin"',
        f'echo -m "not django_pin" && uv run --no-sync pytest {DRIFT_DIR} tests/unit/compile',
        f'echo tests/unit/compile; uv run --no-sync pytest {DRIFT_DIR} -m "not django_pin"',
        f'ASYNCOPATION_REQUIRE_MYSQLDB=0 uv run --no-sync pytest {DRIFT_DIR} tests/unit/compile -m "not django_pin"',
        f"{CANARY_RUN} || true",
        f"{CANARY_RUN} -k 'not mysql'",
        f"echo {CANARY_RUN}",
        "make drift-canary",
    ],
    ids=[
        "no-sync-in-another-segment",
        "marker-in-an-echo-segment",
        "compile-folder-in-an-echo-segment",
        "inline-env-override",
        "failure-swallowed",
        "deselection-appended",
        "whole-command-inside-an-echo",
        "moved-to-a-make-target",
    ],
)
def test_a_canary_drift_command_other_than_the_expected_one_is_a_problem(tmp_path, run):
    # given
    workflow = write_workflow(tmp_path / "ci.yml", drift={CANARY[0]: drift_step(*CANARY, run=run)})
    # when
    problems = drift_step_problems(workflow)
    # then
    assert is_single_problem(problems, "jobs.django-main.django-drift: run is ")


@pytest.mark.parametrize("step", [CANARY, INTEGRATION], ids=["canary", "integration"])
def test_a_changed_drift_step_name_is_not_a_problem(tmp_path, step):
    # given
    renamed = drift_step(*step, name="another name")
    # when
    problems = drift_step_problems(write_workflow(tmp_path / "ci.yml", drift={step[0]: renamed}))
    # then
    assert problems == []


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("if", "${{ false }}"),
        ("continue-on-error", True),
        ("working-directory", "docs"),
        ("shell", "bash -c 'true'"),
        ("with", {"x": "1"}),
        ("timeout-minutes", 1),
        ("uses", "actions/checkout@v5"),
    ],
)
@pytest.mark.parametrize("step", [CANARY, INTEGRATION], ids=["canary", "integration"])
def test_a_drift_step_with_an_extra_key_is_a_problem(tmp_path, step, key, value):
    # given
    extended = drift_step(*step, **{key: value})
    # when
    problems = drift_step_problems(write_workflow(tmp_path / "ci.yml", drift={step[0]: extended}))
    # then
    assert is_single_problem(problems, f"jobs.{step[0]}.{step[1]}: {key} is ")


@pytest.mark.parametrize("key", ["env", "run"])
def test_a_drift_step_without_a_key_is_a_problem(tmp_path, key):
    # given
    stripped = drift_step(*CANARY, drop=(key,))
    # when
    problems = drift_step_problems(write_workflow(tmp_path / "ci.yml", drift={CANARY[0]: stripped}))
    # then
    assert is_single_problem(problems, f"jobs.django-main.django-drift: {key} is '<missing>'")


_PG = ["postgresql"]


@pytest.mark.parametrize(
    ("job", "key", "value", "label"),
    [
        pytest.param("integration", "if", "${{ false }}", "if", id="integration-if"),
        pytest.param(
            "integration",
            "defaults",
            {"run": {"shell": "true {0}"}},
            "defaults",
            id="integration-defaults",
        ),
        pytest.param(
            "integration",
            "continue-on-error",
            True,
            "continue-on-error",
            id="integration-continue-on-error",
        ),
        pytest.param("integration", "needs", "other", "needs", id="integration-needs"),
        pytest.param(
            "integration",
            "strategy",
            {"fail-fast": True, "matrix": {"db": _PG}},
            "strategy.fail-fast",
            id="integration-fail-fast",
        ),
        pytest.param(
            "integration",
            "strategy",
            {"fail-fast": False, "matrix": {"db": _PG, "include": [{"db": "x"}]}},
            "strategy.matrix.include",
            id="integration-matrix-include",
        ),
        pytest.param(
            "integration",
            "strategy",
            {"fail-fast": False, "matrix": {"db": _PG, "exclude": [{"db": "x"}]}},
            "strategy.matrix.exclude",
            id="integration-matrix-exclude",
        ),
        pytest.param(
            "integration",
            "strategy",
            {"fail-fast": False, "matrix": {"db": ["mysql", "mariadb"]}},
            "strategy.matrix.db has postgresql",
            id="integration-matrix-without-postgresql",
        ),
        pytest.param(
            "integration",
            "strategy",
            {"fail-fast": False, "matrix": {"python": ["3.12"]}},
            "strategy.matrix.db has postgresql",
            id="integration-matrix-without-db",
        ),
        pytest.param(
            "integration",
            "env",
            {"PYTEST_ADDOPTS": "-k nothing"},
            "env.PYTEST_ADDOPTS",
            id="integration-pytest-addopts",
        ),
        pytest.param("django-main", "if", "${{ false }}", "if", id="canary-if"),
        pytest.param(
            "django-main",
            "defaults",
            {"run": {"shell": "true {0}"}},
            "defaults",
            id="canary-defaults",
        ),
        pytest.param(
            "django-main",
            "continue-on-error",
            False,
            "continue-on-error",
            id="canary-continue-on-error",
        ),
        pytest.param("django-main", "needs", "other", "needs", id="canary-needs"),
        pytest.param(
            "django-main",
            "strategy",
            {"fail-fast": False},
            "strategy.fail-fast",
            id="canary-strategy",
        ),
        pytest.param(
            "django-main",
            "env",
            {"PYTEST_ADDOPTS": "-k nothing"},
            "env.PYTEST_ADDOPTS",
            id="canary-pytest-addopts",
        ),
    ],
)
def test_a_changed_drift_job_key_is_a_problem(tmp_path, job, key, value, label):
    # given
    workflow = write_workflow(tmp_path / "ci.yml", jobs={job: {key: value}})
    # when
    problems = drift_step_problems(workflow)
    # then
    assert is_single_problem(problems, f"jobs.{job}: {label} is ")


def test_a_matrix_without_postgresql_is_a_problem(tmp_path):
    # given
    matrix = {"fail-fast": False, "matrix": {"db": ["mysql"], "python": ["3.12"]}}
    workflow = write_workflow(tmp_path / "ci.yml", jobs={"integration": {"strategy": matrix}})
    # when
    problems = drift_step_problems(workflow)
    # then
    assert is_single_problem(
        problems, "jobs.integration: strategy.matrix.db has postgresql is False"
    )


def test_pytest_addopts_in_the_workflow_env_is_a_problem(tmp_path):
    # given
    workflow = write_workflow(tmp_path / "ci.yml", top={"env": {"PYTEST_ADDOPTS": "-k nothing"}})
    # when
    problems = drift_step_problems(workflow)
    # then
    assert is_single_problem(problems, "workflow env.PYTEST_ADDOPTS is ")


@pytest.mark.parametrize(
    ("job", "key"),
    [("integration", "needs"), ("django-main", "needs"), ("django-main", "continue-on-error")],
)
def test_a_dropped_drift_job_key_is_a_problem(tmp_path, job, key):
    # given
    workflow = write_workflow(tmp_path / "ci.yml", drop_job_keys={job: (key,)})
    # when
    problems = drift_step_problems(workflow)
    # then
    assert is_single_problem(problems, f"jobs.{job}: {key} is '<missing>'")


@pytest.mark.parametrize(
    ("job", "key", "value"),
    [
        ("integration", "name", "another"),
        ("integration", "env", {"X": "y"}),
        ("integration", "runs-on", "macos-latest"),
        (
            "integration",
            "strategy",
            {
                "fail-fast": False,
                "matrix": {"db": ["postgresql", "mysql"], "python": ["3.12", "3.15"], "x": [1]},
            },
        ),
        ("django-main", "name", "another"),
        ("django-main", "env", {"X": "y"}),
    ],
    ids=[
        "integration-name",
        "integration-env",
        "integration-runs-on",
        "integration-matrix-values",
        "canary-name",
        "canary-env",
    ],
)
def test_a_changed_job_key_that_cannot_skip_the_drift_step_is_not_a_problem(
    tmp_path, job, key, value
):
    # given
    workflow = write_workflow(tmp_path / "ci.yml", jobs={job: {key: value}})
    # when
    problems = drift_step_problems(workflow)
    # then
    assert problems == []


@pytest.mark.parametrize("job", ["integration", "django-main"])
def test_a_step_after_the_drift_step_is_not_a_problem(tmp_path, job):
    # given
    steps = {job: [{"run": CANARY_INSTALL_RUN}, drift_step(*DRIFT[job]), {"run": "echo done"}]}
    # when
    problems = drift_step_problems(write_workflow(tmp_path / "ci.yml", steps))
    # then
    assert problems == []


def test_unrelated_top_level_keys_are_not_a_problem(tmp_path):
    # given
    top = {"concurrency": "ci", "env": {"X": "y"}, "permissions": {"contents": "read"}}
    # when
    problems = drift_step_problems(write_workflow(tmp_path / "ci.yml", top=top))
    # then
    assert problems == []


def test_a_workflow_level_defaults_is_a_problem(tmp_path):
    # given
    workflow = write_workflow(tmp_path / "ci.yml", top={"defaults": {"run": {"shell": "true"}}})
    # when
    problems = drift_step_problems(workflow)
    # then
    assert is_single_problem(problems, "workflow defaults is ")


def test_a_canary_drift_step_not_right_after_the_django_main_install_is_a_problem(tmp_path):
    # given
    steps = {"django-main": [{"run": "uv sync"}, drift_step(*CANARY)]}
    # when
    problems = drift_step_problems(write_workflow(tmp_path / "ci.yml", steps))
    # then
    assert is_single_problem(problems, "jobs.django-main: the step before the drift step is")


def test_an_inserted_step_between_install_and_canary_drift_is_a_problem(tmp_path):
    # given
    steps = {"django-main": [{"run": CANARY_INSTALL_RUN}, {"run": "uv sync"}, drift_step(*CANARY)]}
    # when
    problems = drift_step_problems(write_workflow(tmp_path / "ci.yml", steps))
    # then
    assert is_single_problem(
        problems, "jobs.django-main: the step before the drift step is 'uv sync'"
    )


def test_a_canary_drift_step_as_the_first_step_is_a_problem(tmp_path):
    # given
    steps = {"django-main": [drift_step(*CANARY)]}
    # when
    problems = drift_step_problems(write_workflow(tmp_path / "ci.yml", steps))
    # then
    assert is_single_problem(problems, "jobs.django-main: the step before the drift step is None")


@pytest.mark.parametrize(
    "env",
    [
        {},
        {"ASYNCOPATION_REQUIRE_MYSQLDB": "0"},
        {"ASYNCOPATION_REQUIRE_MYSQLDB": "1", "X": "y"},
        None,
    ],
    ids=["empty", "zero", "extra-variable", "missing"],
)
@pytest.mark.parametrize("step", [CANARY, INTEGRATION], ids=["canary", "integration"])
def test_a_drift_step_with_a_changed_env_is_a_problem(tmp_path, step, env):
    # given
    changed = drift_step(*step, env=env)
    workflow = write_workflow(tmp_path / "ci.yml", drift={step[0]: changed})
    # when
    problems = drift_step_problems(workflow)
    # then
    assert is_single_problem(problems, f"jobs.{step[0]}.{step[1]}: env is ")


def test_an_integration_drift_command_other_than_the_expected_one_is_a_problem(tmp_path):
    # given
    step = drift_step(*INTEGRATION, run=f"uv run pytest {DRIFT_DIR}")
    workflow = write_workflow(tmp_path / "ci.yml", drift={INTEGRATION[0]: step})
    # when
    problems = drift_step_problems(workflow)
    # then
    assert is_single_problem(problems, "jobs.integration.integration-drift: run is ")


@pytest.mark.parametrize("step", [CANARY, INTEGRATION], ids=["canary", "integration"])
def test_a_workflow_without_the_drift_step_id_is_rejected(tmp_path, step):
    # given
    renamed = drift_step(*step, id="other")
    workflow = write_workflow(tmp_path / "ci.yml", drift={step[0]: renamed})
    # when / then
    with pytest.raises(
        RuntimeError, match=r"exactly one step with id '.*' in jobs\.[\w-]+ of ci\.yml, found 0"
    ):
        drift_step_problems(workflow)


@pytest.mark.parametrize("step", [CANARY, INTEGRATION], ids=["canary", "integration"])
def test_a_workflow_with_a_duplicated_drift_step_id_is_rejected(tmp_path, step):
    # given
    workflow = write_workflow(
        tmp_path / "ci.yml", {step[0]: [drift_step(*step), drift_step(*step)]}
    )
    # when / then
    with pytest.raises(
        RuntimeError, match=r"exactly one step with id '.*' in jobs\.[\w-]+ of ci\.yml, found 2"
    ):
        drift_step_problems(workflow)


@pytest.mark.parametrize(
    "text",
    ["", "jobs:\n", "jobs: []\n", "jobs:\n  django-main: 3\n", "- a\n"],
    ids=["empty", "no-job", "jobs-list", "job-scalar", "top-level-list"],
)
def test_a_workflow_without_a_job_mapping_is_rejected(tmp_path, text):
    # given
    workflow = tmp_path / "ci.yml"
    workflow.write_text(text, encoding="utf-8")
    # when / then
    with pytest.raises(RuntimeError, match=r"no mapping at jobs\."):
        drift_step_problems(workflow)


def test_a_job_whose_steps_are_not_a_list_has_no_drift_step(tmp_path):
    # given
    workflow = tmp_path / "ci.yml"
    workflow.write_text("jobs:\n  integration:\n    steps: 3\n", encoding="utf-8")
    # when / then
    with pytest.raises(RuntimeError, match=r"found 0"):
        drift_step_problems(workflow)
