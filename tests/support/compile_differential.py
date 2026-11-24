"""Compile-only wrapper vs Django's real wrapper: same SQL, no sync connection (ADR-0008)."""

from __future__ import annotations

import contextlib
from collections.abc import Callable, Generator
from dataclasses import dataclass
from typing import Any
from unittest import mock

from django.db import connections, transaction
from django.db.models import Count
from django.db.models.sql import Query
from django.db.models.sql.compiler import SQLCompiler

from asyncopation.compile.postgresql import wrapper_for
from asyncopation.compile.scope import compile_scope
from asyncopation.compile.server_info import PostgreSQLServerInfo
from harness.catalog.models import Book
from tests.support.connect_counter import count_sync_connection_attempts
from tests.support.strategies import build_queryset

type Outcome = tuple[Any, ...]


@dataclass(frozen=True)
class Comparison:
    real: Outcome
    only: Outcome
    sync_attempts: int
    compilers: int = 0
    foreign_compilers: int = 0

    @property
    def verdict(self) -> tuple[Outcome, int, int, bool]:
        """(compile-only outcome, sync attempts, compilers bound to another connection, any built)."""
        return (self.only, self.sync_attempts, self.foreign_compilers, self.compilers > 0)

    @property
    def expected(self) -> tuple[Outcome, int, int, bool]:
        """What `verdict` must equal: the real outcome, no attempts, all compilers on the wrapper."""
        return (self.real, 0, 0, True)


class _Captured(Exception):
    def __init__(self, query: Any) -> None:
        self.query = query


def _capture(query: Any, *args: Any, **kwargs: Any) -> Any:
    raise _Captured(query)


def count_outer_query(build: Callable[[], Any]) -> Any:
    """The query Django compiles for `build().count()` (the outer `AggregateQuery` when sliced,
    distinct, annotated or combined; the annotated query itself otherwise)."""
    queryset = build()
    with mock.patch.object(Query, "get_compiler", _capture):
        try:
            queryset.query.get_count(using="default")
        except _Captured as captured:
            return captured.query
    raise AssertionError("count() never asked for a compiler")


def outcome(query: Any, alias: str) -> Outcome:
    try:
        sql, params = query.get_compiler(using=alias).as_sql()
    except Exception as exc:
        return ("raises", type(exc).__name__)
    return ("sql", sql, tuple(params))


def compare_compilation(
    build_query: Callable[[], Any], *, autocommit: bool = True, alias: str = "default"
) -> Comparison:
    """Compile `build_query()` on the real wrapper and on the compile-only wrapper in a scope."""
    real_query, only_query = build_query(), build_query()
    real_wrapper = connections[alias]
    info = PostgreSQLServerInfo(pg_version=real_wrapper.pg_version)  # ty: ignore[missing-attribute]
    wrapper = wrapper_for(alias, real_wrapper.settings_dict, info)
    guard = contextlib.nullcontext() if autocommit else transaction.atomic(using=alias)
    with guard:
        real = outcome(real_query, alias)
    seen: list[Any] = []
    with (
        count_sync_connection_attempts() as counter,
        _record_compilers(seen),
        compile_scope(alias, wrapper, autocommit=autocommit),
    ):
        only = outcome(only_query, alias)
    foreign = sum(connection is not wrapper for connection in seen)
    return Comparison(real, only, counter.count, len(seen), foreign)


@contextlib.contextmanager
def _record_compilers(seen: list[Any]) -> Generator[None]:
    """Record the connection of every SQLCompiler constructed while active."""
    original = SQLCompiler.__init__

    def recording(self: Any, query: Any, connection: Any, *args: Any, **kwargs: Any) -> None:
        seen.append(connection)
        original(self, query, connection, *args, **kwargs)

    with mock.patch.object(SQLCompiler, "__init__", recording):
        yield


def query_of(spec: Any) -> Any:
    return build_queryset(spec).query


def outer_of(build: Callable[[], Any]) -> Any:
    return count_outer_query(build)


def exists_of(build: Callable[[], Any]) -> Any:
    return build().query.exists()


def sliced_books() -> Any:
    return Book.objects.order_by("pk")[:3]


def distinct_books() -> Any:
    return Book.objects.distinct()


def annotated_books() -> Any:
    return Book.objects.annotate(n_tags=Count("tags"))


def union_books() -> Any:
    return Book.objects.filter(pk__lt=5).union(Book.objects.filter(pk__gt=50))


def plain_books() -> Any:
    return Book.objects.filter(published=True)


def for_update_query() -> Any:
    return Book.objects.select_for_update().order_by("pk").query


def for_update_of_query() -> Any:
    return Book.objects.select_for_update(of=("self",), nowait=True).order_by("pk").query


def filtered_books() -> Any:
    return Book.objects.filter(published=True, pages__gte=100)
