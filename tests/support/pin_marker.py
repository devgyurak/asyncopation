"""Collect the django-drift tests under a marker expression, as the django-main canary does."""

from __future__ import annotations

import os
import subprocess
import sys
from functools import cache
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DRIFT_DIR = "tests/unit/django_drift"
# Must match the `django_pin` tests in tests/unit/django_drift/test_ledger_drift.py.
PIN_TESTS = frozenset(
    {
        "test_installed_django_satisfies_the_pyproject_pin",
        "test_django_classifiers_match_the_pyproject_pin",
    }
)
ASGIREF_VERSION_TEST = "test_installed_dependency_is_the_version_the_ledger_was_verified_against"


@cache
def collected_test_names(marker_expression: str) -> frozenset[str]:
    """Function names (parameters stripped) collected from the drift folder under ``-m``."""
    result = subprocess.run(
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
    if result.returncode != 0:
        message = f"collection failed ({result.returncode}):\n{result.stdout}\n{result.stderr}"
        raise RuntimeError(message)
    node_ids = [line for line in result.stdout.splitlines() if "::" in line]
    return frozenset(node_id.split("::")[-1].split("[")[0] for node_id in node_ids)
