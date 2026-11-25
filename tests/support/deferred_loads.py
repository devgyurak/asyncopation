"""Guards that make a lazy load of a deferred field fail a test."""

from __future__ import annotations

from typing import Any

from django.db.models.query_utils import DeferredAttribute


def forbid_deferred_loads(monkeypatch: Any) -> None:
    """Fail on any lazy load of a deferred field, whatever the fetch mode.

    FETCH_ONE loads through ``fetch_one`` (``refresh_from_db``); FETCH_PEERS loads through
    ``fetch_many`` (a bulk query that does not go through ``refresh_from_db``). Both are blocked.
    They are Django 6.1 internals (``DeferredAttribute``), verified for the pinned minor; a rename
    fails loudly in ``monkeypatch.setattr``. Tests only: not a ``src/`` use, so no ledger entry.
    """

    def refuse(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("a deferred field was loaded")

    monkeypatch.setattr(DeferredAttribute, "fetch_one", refuse)
    monkeypatch.setattr(DeferredAttribute, "fetch_many", refuse)
