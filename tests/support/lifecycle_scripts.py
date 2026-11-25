"""Isolated-interpreter checks (`python -I -c`): what an import or a first open pulls in."""

from __future__ import annotations

import subprocess
import sys
from dataclasses import dataclass
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
from asyncopation.connection import configure, engines, setup
from tests.support.lifecycle import Recorder, Script, make_engine
engine = make_engine(Recorder(), Script())
engines.FACTORIES["django.db.backends.mysql"] = lambda: engine
configure()
asyncio.run(setup())
"""
    + _REPORT
)

PSYCOPG_MISSING = (
    _HEAD
    + """
sys.modules["psycopg"] = None
sys.modules["psycopg_pool"] = None
from django.conf import settings
settings.configure(
    DATABASES={"default": {"ENGINE": "django.db.backends.postgresql", "NAME": "x"}},
    USE_TZ=True, TIME_ZONE="UTC", INSTALLED_APPS=[],
)
import django
django.setup()
from asyncopation.apps import AsyncopationConfig
import asyncopation
AsyncopationConfig("asyncopation", asyncopation).ready()
print("configured")
"""
)


@dataclass(frozen=True)
class Ran:
    returncode: int
    stdout: str
    stderr: str


def import_script(statement: str) -> str:
    return _HEAD + statement + "\n" + _REPORT


def run_isolated(script: str) -> Ran:
    """Run `script` in a clean interpreter (`-I`)."""
    done = subprocess.run(
        [sys.executable, "-I", "-c", script], capture_output=True, text=True, timeout=60
    )
    return Ran(done.returncode, done.stdout.strip(), done.stderr.strip())
