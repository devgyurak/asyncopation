"""Prepare the dedicated benchmark database (`bench_asyncopation`) with the seed dataset."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BENCH_DB = "bench_asyncopation"


def bench_env(**extra: str) -> dict[str, str]:
    base = {
        "ASYNCOPATION_DB_NAME": BENCH_DB,
        "PYTHONPATH": str(ROOT),
        "DJANGO_SETTINGS_MODULE": "harness.settings",
        # Persistent connections, as in production; avoids ephemeral-port exhaustion under load.
        "ASYNCOPATION_CONN_MAX_AGE": "600",
    }
    return os.environ | base | extra


def ensure_bench_db() -> None:
    """Create database + tables if needed and reset the seed dataset. Idempotent."""
    subprocess.run(
        [sys.executable, "-m", "harness.seed", "--create-database", "--create"],
        cwd=ROOT,
        env=bench_env(),
        check=True,
    )
