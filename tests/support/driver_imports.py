"""Import hygiene checks in a clean interpreter."""

from __future__ import annotations

import subprocess
import sys


def imported_driver_modules(module: str) -> str:
    """Driver modules loaded after importing `module` in a clean interpreter."""
    code = (
        "import importlib, sys\n"
        f"importlib.import_module({module!r})\n"
        "drivers = ('psycopg', 'psycopg2', 'psycopg_pool', 'asyncmy', 'MySQLdb')\n"
        "print(sorted(m for m in sys.modules if m.split('.')[0] in drivers))\n"
    )
    done = subprocess.run(
        [sys.executable, "-I", "-c", code], capture_output=True, text=True, check=True
    )
    return done.stdout.strip()
