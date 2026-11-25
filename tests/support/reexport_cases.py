"""Parameter sets derived from the guard's psycopg re-export table (tools/guards/reexports.py)."""

from __future__ import annotations

import pytest

from tools.guards.reexports import DRIVER_REEXPORTS, FORBIDDEN_EVERYWHERE

NAME_PARAMS = [
    pytest.param(module, name, id=f"{module.removeprefix('django.')}:{name}")
    for module, names in DRIVER_REEXPORTS.items()
    for name in sorted(names - {"*"} - FORBIDDEN_EVERYWHERE)  # those have their own rule
]
MODULE_PARAMS = [
    pytest.param(module, id=module.removeprefix("django.")) for module in DRIVER_REEXPORTS
]
