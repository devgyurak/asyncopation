"""Isolated-interpreter checks (`python -I -c`): what an import or a first open pulls in."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DRIVER_PACKAGES = "{'psycopg', 'psycopg_pool', 'psycopg_binary', 'asyncmy'}"

_REPORT = f"print(sorted(m for m in sys.modules if m.split('.')[0] in {DRIVER_PACKAGES}))"
_HEAD = f"import sys\nsys.path[:0] = [{str(ROOT)!r}, {str(ROOT / 'src')!r}]\n"

MYSQL_FAKE_OPEN = (
    _HEAD
    + """
import asyncio
from django.conf import settings
settings.configure(
    DATABASES={"default": {"ENGINE": "django.db.backends.mysql", "NAME": "x"}},
    USE_TZ=True, TIME_ZONE="UTC", INSTALLED_APPS=[],
)
import django
django.setup()
from asyncopation.connection import configure, engines
from asyncopation.connection.lifecycle import ensure_driver
from tests.support.lifecycle import Recorder, Script, make_engine
engine = make_engine(Recorder(), Script())
engines.FACTORIES["django.db.backends.mysql"] = lambda: engine
configure()
asyncio.run(ensure_driver("default"))
"""
    + _REPORT
)


def import_script(statement: str) -> str:
    return _HEAD + statement + "\n" + _REPORT


def run_isolated(script: str) -> str:
    """Run `script` in a clean interpreter (`-I`); return its stripped stdout (stderr carries log noise only)."""
    done = subprocess.run(
        [sys.executable, "-I", "-c", script], capture_output=True, text=True, timeout=60
    )
    return done.stdout.strip()
