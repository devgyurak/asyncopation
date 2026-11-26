"""QuerySets that share a filled result cache in a user fetch mode (unit tests of the engine clones)."""

from __future__ import annotations

from typing import Any

from django.db.models import FETCH_PEERS  # ty: ignore[missing-module-member] - missing from django-types

from asyncopation._django import query as dq


def filled_in_a_user_fetch_mode(queryset: Any) -> Any:
    """`queryset` with a filled cache and a fetch mode that is not FETCH_RAISE (what a prefetched
    manager or a synchronously evaluated QuerySet hands the wrapper)."""
    dq.set_cached_result(queryset, [])
    dq.set_fetch_mode(queryset, FETCH_PEERS)
    return queryset
