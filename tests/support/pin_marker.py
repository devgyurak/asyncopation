"""Collect the django-drift tests under a marker expression, as the django-main canary does.

Also reads the canary's drift step out of ``.github/workflows/ci.yml``.
"""

from __future__ import annotations

import copy
import os
import subprocess
import sys
from collections.abc import Mapping, Sequence
from functools import cache
from pathlib import Path
from typing import Any

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
_REQUIRE_MYSQLDB = {"ASYNCOPATION_REQUIRE_MYSQLDB": "1"}
# The two CI steps that run the MySQL wrapper drift suite, as the whole expected step mapping (the
# ids are set in .github/workflows/ci.yml; only `name` is free).
INTEGRATION_STEP: dict[str, Any] = {
    "id": "integration-drift",
    "env": _REQUIRE_MYSQLDB,
    "run": f"uv run pytest {DRIFT_DIR} tests/unit/compile",
}
CANARY_INSTALL_RUN = 'uv pip install "django @ git+https://github.com/django/django@main"'
CANARY_STEP: dict[str, Any] = {
    "id": "django-drift",
    "env": _REQUIRE_MYSQLDB,
    "run": f'uv run --no-sync pytest {DRIFT_DIR} tests/unit/compile -m "not django_pin"',
}
# (job, expected step, the `run:` of the step right before it or None, expected job keys besides
# `name` and `steps`). The canary's premise is that the drift step runs right after Django main is
# installed; its job is allowed to fail (`continue-on-error`), the integration job is not.
DRIFT_STEPS: tuple[tuple[str, dict[str, Any], str | None, dict[str, Any]], ...] = (
    (
        "integration",
        INTEGRATION_STEP,
        None,
        {
            "runs-on": "ubuntu-latest",
            "needs": "check",
            "strategy": {
                "fail-fast": False,
                "matrix": {"db": ["postgresql", "mysql", "mariadb"], "python": ["3.12", "3.14"]},
            },
            "env": {"ASYNCOPATION_DB": "${{ matrix.db }}", "HYPOTHESIS_PROFILE": "ci"},
        },
    ),
    (
        "django-main",
        CANARY_STEP,
        CANARY_INSTALL_RUN,
        {"runs-on": "ubuntu-latest", "needs": "check", "continue-on-error": True},
    ),
)
_MISSING = "<missing>"


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


def _differences(label: str, actual: dict[str, object], expected: dict[str, object]) -> list[str]:
    """One message per key whose value differs, is missing or is extra (``name`` is free)."""
    keys = sorted((set(actual) | set(expected)) - {"name"})
    return [
        f"{label}: {key} is {actual.get(key, _MISSING)!r}, expected {expected.get(key, _MISSING)!r}"
        for key in keys
        if actual.get(key, _MISSING) != expected.get(key, _MISSING)
    ]


def drift_step_problems(workflow: Path = CI_WORKFLOW) -> list[str]:
    """What differs between the workflow's drift steps (and their jobs) and ``DRIFT_STEPS``.

    Each step is found by id and compared as a whole mapping except ``name``: ``run``, ``env`` and
    any other key (``if``, ``continue-on-error``, ``working-directory``, ``shell``, ``with``, ...)
    must be exactly as expected, so no shell parsing is needed. The job's own keys (besides ``name``
    and ``steps``) are compared the same way, a workflow-level ``defaults`` is a problem, and the
    canary's drift step must follow the Django main install step. Empty when nothing differs.
    """
    document = yaml.safe_load(workflow.read_text(encoding="utf-8")) or {}
    problems: list[str] = []
    if isinstance(document, dict) and "defaults" in document:
        problems.append(f"workflow defaults is {document['defaults']!r}, expected {_MISSING!r}")
    for job, expected_step, previous_run, expected_job in DRIFT_STEPS:
        step = find_step(workflow, job, expected_step["id"])
        steps = document["jobs"][job]["steps"]
        problems += _differences(f"jobs.{job}.{expected_step['id']}", step, expected_step)
        problems += _differences(
            f"jobs.{job}",
            {k: v for k, v in document["jobs"][job].items() if k != "steps"},
            expected_job,
        )
        before = steps[steps.index(step) - 1] if steps.index(step) else {}
        if previous_run is not None and before.get("run") != previous_run:
            problems.append(
                f"jobs.{job}: the step before the drift step is {before.get('run')!r}, expected {previous_run!r}"
            )
    return problems


def is_single_problem(problems: list[str], prefix: str) -> bool:
    """Whether ``problems`` is exactly one message and it starts with ``prefix``."""
    return len(problems) == 1 and problems[0].startswith(prefix)


def drift_step(
    job: str, step_id: str, *, drop: tuple[str, ...] = (), **overrides: object
) -> dict[str, object]:
    """The expected step of ``DRIFT_STEPS`` as a workflow step mapping, with ``overrides`` applied
    and the keys in ``drop`` removed."""
    expected = {j: s for j, s, _, _ in DRIFT_STEPS if s["id"] == step_id}[job]
    merged = copy.deepcopy(expected) | overrides
    return {key: value for key, value in merged.items() if key not in drop}


def write_workflow(
    path: Path,
    steps: Mapping[str, Sequence[Mapping[str, object]]] | None = None,
    jobs: Mapping[str, Mapping[str, object]] | None = None,
    drift: Mapping[str, Mapping[str, object]] | None = None,
    top: dict[str, object] | None = None,
) -> Path:
    """A minimal workflow with the expected drift steps and job keys.

    The default steps of a job are the step before the drift step, then the drift step; ``drift``
    replaces the drift step of a job, ``steps`` all of its steps, ``jobs`` adds or replaces job
    keys and ``top`` adds workflow-level keys.
    """
    document: dict[str, Any] = {"jobs": {}} | (top or {})
    for job, step, previous_run, job_keys in DRIFT_STEPS:
        default = [
            {"run": previous_run or "uv sync"},
            (drift or {}).get(job, drift_step(job, step["id"])),
        ]
        document["jobs"][job] = (
            copy.deepcopy(job_keys)
            | {"steps": (steps or {}).get(job, default)}
            | dict((jobs or {}).get(job, {}))
        )
    path.write_text(yaml.safe_dump(document), encoding="utf-8")
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
