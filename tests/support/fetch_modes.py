"""Django 6.1 fetch modes (public API) for tests; the installed type stubs predate them."""

from __future__ import annotations

from importlib import import_module
from typing import Any

_models: Any = import_module("django.db.models")

FETCH_ONE: Any = _models.FETCH_ONE
FETCH_PEERS: Any = _models.FETCH_PEERS
FETCH_RAISE: Any = _models.FETCH_RAISE
