"""Counts the queries a snapshot of real ``only()``/``defer()`` rows runs (sync ORM)."""

from __future__ import annotations

from typing import Any

from django.db import connection
from django.test.utils import CaptureQueriesContext

from tests.support.differential import QuerySetFactory
from tests.support.snapshot import snapshot_result


def evaluate_and_snapshot_query_counts(
    build: QuerySetFactory, fetch_mode: Any
) -> tuple[int, int, int]:
    """``(rows, queries to evaluate the queryset, queries run by snapshotting its rows)``."""
    with CaptureQueriesContext(connection) as context:
        rows = list(build().fetch_mode(fetch_mode))
        evaluated = len(context)
        snapshot_result(rows)
    return len(rows), evaluated, len(context) - evaluated
