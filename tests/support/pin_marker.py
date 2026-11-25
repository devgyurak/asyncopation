"""Collect the django-drift tests under a marker expression, as the django-main canary does.

Also reads the canary's drift step out of ``.github/workflows/ci.yml``.
"""

from __future__ import annotations

import os
import subprocess
import sys
from functools import cache
from pathlib import Path

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


def canary_drift_command(workflow: Path = CI_WORKFLOW) -> str:
    """The ``run:`` command of the canary step that runs the drift tests (exactly one)."""
    lines = workflow.read_text(encoding="utf-8").splitlines()
    matches = [line.strip() for line in lines if "run:" in line and f"pytest {DRIFT_DIR}" in line]
    if len(matches) != 1:
        message = f"expected exactly one drift step in {workflow.name}, found {len(matches)}"
        raise RuntimeError(message)
    return matches[0]


def write_workflow(path: Path, drift_steps: int) -> Path:
    """A minimal workflow file with ``drift_steps`` drift ``run:`` lines."""
    step = f'      - run: uv run --no-sync pytest {DRIFT_DIR} -m "not django_pin"\n'
    path.write_text("steps:\n" + step * drift_steps, encoding="utf-8")
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
