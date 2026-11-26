"""Collect the django-drift tests under a marker expression, as the django-main canary does.

Also reads the canary's drift step out of ``.github/workflows/ci.yml``.
"""

from __future__ import annotations

import os
import subprocess
import sys
from functools import cache
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
DRIFT_DIR = "tests/unit/django_drift"
# Must match the `django_pin` tests in tests/unit/django_drift/test_ledger_drift.py.
LEDGER_MODULE = f"{DRIFT_DIR}/test_ledger_drift.py"
PIN_TESTS = frozenset(
    {
        f"{LEDGER_MODULE}::test_installed_django_satisfies_the_pyproject_pin",
        f"{LEDGER_MODULE}::test_django_classifiers_match_the_pyproject_pin",
    }
)
ASGIREF_VERSION_TEST = (
    f"{LEDGER_MODULE}::test_installed_dependency_is_the_version_the_ledger_was_verified_against"
)
CI_WORKFLOW = ROOT / ".github" / "workflows" / "ci.yml"
# (job, step id, exact `run:` text, exact `env:`) of the two CI steps that run the MySQL wrapper
# drift suite; the ids are set in .github/workflows/ci.yml.
_REQUIRE_MYSQLDB = {"ASYNCOPATION_REQUIRE_MYSQLDB": "1"}
DRIFT_STEPS = (
    (
        "integration",
        "integration-drift",
        f"uv run pytest {DRIFT_DIR} tests/unit/compile",
        _REQUIRE_MYSQLDB,
    ),
    (
        "django-main",
        "django-drift",
        f'uv run --no-sync pytest {DRIFT_DIR} tests/unit/compile -m "not django_pin"',
        _REQUIRE_MYSQLDB,
    ),
)


@cache
def collected_test_names(marker_expression: str) -> frozenset[str]:
    """``path::function`` ids (parameters stripped) collected from the drift folder under ``-m``."""
    try:
        result = _collect(marker_expression)
    except subprocess.TimeoutExpired as error:
        message = f"collection timed out after {error.timeout}s"
        raise RuntimeError(message) from error
    if result.returncode != 0:
        message = f"collection failed ({result.returncode}):\n{result.stdout}\n{result.stderr}"
        raise RuntimeError(message)
    node_ids = [line for line in result.stdout.splitlines() if "::" in line]
    return frozenset(node_id.split("[")[0] for node_id in node_ids)


def find_step(workflow: Path, job: str, step_id: str) -> dict[str, object]:
    """The step with ``id: step_id`` of ``job``: exactly one, or a ``RuntimeError``.

    A malformed workflow (no such job, ``steps`` not a list) raises instead of reading as empty.
    """
    document = yaml.safe_load(workflow.read_text(encoding="utf-8")) or {}
    jobs = document.get("jobs") if isinstance(document, dict) else None
    mapping = jobs.get(job) if isinstance(jobs, dict) else None
    if not isinstance(mapping, dict):
        message = f"{workflow.name} has no mapping at jobs.{job}"
        raise RuntimeError(message)
    steps = mapping.get("steps")
    matches = [
        step
        for step in (steps if isinstance(steps, list) else [])
        if isinstance(step, dict) and step.get("id") == step_id
    ]
    if len(matches) != 1:
        message = f"expected exactly one step with id {step_id!r} in jobs.{job} of {workflow.name}, found {len(matches)}"
        raise RuntimeError(message)
    return matches[0]


def drift_step_problems(workflow: Path = CI_WORKFLOW) -> list[str]:
    """What differs between the workflow's drift steps and ``DRIFT_STEPS``; empty when none does.

    The steps are found by id and their ``run`` text and ``env`` are compared exactly, so no shell
    parsing is needed: any change to the command, an extra command in it or an overriding ``env``
    is a difference to be made on purpose, together with ``DRIFT_STEPS``.
    """
    problems: list[str] = []
    for job, step_id, expected_run, expected_env in DRIFT_STEPS:
        step = find_step(workflow, job, step_id)
        run = str(step.get("run", "")).strip()
        if run != expected_run:
            problems.append(f"jobs.{job}.{step_id}: run is {run!r}, expected {expected_run!r}")
        if step.get("env") != expected_env:
            problems.append(
                f"jobs.{job}.{step_id}: env is {step.get('env')!r}, expected {expected_env!r}"
            )
    return problems


def drift_step(job: str, step_id: str, **overrides: object) -> dict[str, object]:
    """The expected step of ``DRIFT_STEPS`` as a workflow step mapping, with ``overrides`` applied."""
    expected = {(j, i): (r, e) for j, i, r, e in DRIFT_STEPS}[job, step_id]
    return {"id": step_id, "env": dict(expected[1]), "run": expected[0]} | overrides


def write_workflow(path: Path, steps: dict[str, list[dict[str, object]]] | None = None) -> Path:
    """A minimal workflow with the expected drift steps; ``steps`` replaces the steps of a job."""
    default = {
        job: [{"run": "uv sync"}, drift_step(job, step_id)] for job, step_id, _, _ in DRIFT_STEPS
    }
    path.write_text(
        yaml.safe_dump(
            {"jobs": {job: {"steps": s} for job, s in (default | (steps or {})).items()}}
        ),
        encoding="utf-8",
    )
    return path


def _collect(marker_expression: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            DRIFT_DIR,
            "-o",
            "addopts=",
            "--strict-markers",
            "-m",
            marker_expression,
            "--collect-only",
            "-q",
            "-p",
            "no:cacheprovider",
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        env={**os.environ, "PYTEST_ADDOPTS": ""},
        timeout=60,
    )
