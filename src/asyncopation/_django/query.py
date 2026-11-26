"""Accessors for the QuerySet and compiler internals the evaluation engine reads (ADR-0002, 0007).

Every private attribute and non-public symbol the engine needs goes through here, so a Django
upgrade breaks in one place. Nothing in this module runs I/O: the compiler is only asked about
state that ``as_sql()`` has already computed.
"""

# Ported from django/db/models/query.py::QuerySet.get, QuerySet.first @ 6.1.3
# Changes: the part of get() / first() before the evaluation, without `connections[...]` (get_clone,
# first_queryset); see engine/read.py for the rest.

from __future__ import annotations

from enum import Enum
from typing import Any

from django.db import NotSupportedError
from django.db.models import FETCH_RAISE  # ty: ignore[missing-module-member] - missing from django-types
from django.db.models.query import (
    MAX_GET_RESULTS,  # ty: ignore[missing-module-member] - missing from django-types
    FlatValuesListIterable,
    ModelIterable,
    NamedValuesListIterable,
    ValuesIterable,
    ValuesListIterable,
    _get_from_db,  # ty: ignore[missing-module-member] - missing from django-types
    get_related_populators,  # ty: ignore[missing-module-member] - missing from django-types
)
from django.db.models.sql.compiler import SQLAggregateCompiler
from django.db.models.utils import create_namedtuple_class  # ty: ignore[missing-module-member] - missing from django-types
from django.utils.connection import ConnectionDoesNotExist

__all__ = [
    "MAX_GET_RESULTS",
    "ConnectionDoesNotExist",
    "IterableKind",
    "annotation_col_map",
    "cached_result",
    "clone_query",
    "col_count",
    "compile_copy",
    "copy_filter_sticky",
    "first_queryset",
    "from_db_callable",
    "get_clone",
    "has_extra_select",
    "has_prefetch",
    "iterable_kind",
    "klass_info",
    "known_related_objects",
    "limit_results",
    "namedtuple_class",
    "queryset_fetch_mode",
    "queryset_fields",
    "raise_on_fetch",
    "related_populators",
    "select",
    "select_for_update",
    "set_cached_result",
    "set_fetch_mode",
    "set_peers",
    "supports_select_for_update_with_limit",
    "values_names",
]


class IterableKind(Enum):
    """Which of Django's result iterables a QuerySet uses (decided by exact class identity)."""

    MODEL = "model"
    VALUES = "values"
    VALUES_LIST = "values_list"
    FLAT = "flat"
    NAMED = "named"


_KINDS = {
    ModelIterable: IterableKind.MODEL,
    ValuesIterable: IterableKind.VALUES,
    ValuesListIterable: IterableKind.VALUES_LIST,
    FlatValuesListIterable: IterableKind.FLAT,
    NamedValuesListIterable: IterableKind.NAMED,
}


def iterable_kind(queryset: Any) -> IterableKind:
    """Classify ``queryset._iterable_class``; subclasses and other iterables are unsupported."""
    cls = queryset._iterable_class
    try:
        return _KINDS[cls]
    except KeyError:
        raise NotImplementedError(
            f"asyncopation cannot evaluate a QuerySet whose iterable class is {cls.__qualname__}"
        ) from None


def cached_result(queryset: Any) -> list[Any] | None:
    return queryset._result_cache


def set_cached_result(queryset: Any, result: list[Any]) -> None:
    queryset._result_cache = result


def has_prefetch(queryset: Any) -> bool:
    return bool(queryset._prefetch_related_lookups)


def queryset_fetch_mode(queryset: Any) -> Any:
    return queryset._fetch_mode


def set_fetch_mode(queryset: Any, mode: Any) -> None:
    """Set the fetch mode in place (``QuerySet.fetch_mode`` would clone and consume a sticky filter)."""
    queryset._fetch_mode = mode


def raise_on_fetch(queryset: Any) -> Any:
    """Make an engine-built clone ``FETCH_RAISE`` in place (it saves one clone: the clone is ours,
    never shared) and return it.

    The wrapper may share a filled-cache QuerySet in a user fetch mode (ADR-0006), and a clone of it
    inherits that mode; the instances the engine itself loads must never lazy-load synchronously.
    """
    set_fetch_mode(queryset, FETCH_RAISE)
    return queryset


def copy_filter_sticky(target: Any, source: Any) -> None:
    """Give ``target``'s Query the pending sticky-filter flag of ``source``'s (``QuerySet._chain``)."""
    target.query.filter_is_sticky = source.query.filter_is_sticky


def compile_copy(queryset: Any) -> Any:
    """A private copy of the QuerySet's Query to compile: ``as_sql()`` mutates the Query it runs on
    (combined queries gain ``__orderbycol<N>`` annotations), and concurrent evaluations of one
    QuerySet must not see each other's. ``Query.clone()`` shares the ``selected`` dict between the
    copies, and compiling adds the ``__orderbycol<N>`` entries to it: it is copied here, or the
    clone's combined queries (which lack those annotations) would no longer match it."""
    return clone_query(queryset.query)


def clone_query(query: Any) -> Any:
    """``query.clone()`` with its own ``selected`` dict (``Query.clone()`` shares it with the original,
    and compiling or ``exists()`` / ``get_aggregation`` add entries to it)."""
    clone = query.clone()
    if clone.selected:
        clone.selected = dict(clone.selected)
    return clone


def queryset_fields(queryset: Any) -> Any:
    return queryset._fields


def known_related_objects(queryset: Any) -> Any:
    return queryset._known_related_objects


def values_names(query: Any) -> list[str]:
    """The key names of a ``values()`` row (``ValuesIterable.__iter__``, Django 6.1).

    Must be read from the Query BEFORE ``as_sql()``: compiling a combined query ordered by a column
    that is not selected adds ``__orderbycol<N>`` annotations to it, which are not row keys.
    """
    if query.selected:
        return list(query.selected)
    # extra(select=...) cols are always at the start of the row.
    return [*query.extra_select, *query.values_select, *query.annotation_select]


def namedtuple_class(*names: str) -> Any:
    return create_namedtuple_class(*names)


def select(compiler: Any) -> Any:
    return compiler.select


def klass_info(compiler: Any) -> Any:
    return compiler.klass_info


def annotation_col_map(compiler: Any) -> Any:
    return compiler.annotation_col_map


def col_count(compiler: Any) -> int:
    return compiler.col_count


def has_extra_select(compiler: Any) -> bool:
    # Set by `pre_sql_setup()`, which every compiler runs except SQLAggregateCompiler (the outer query
    # of a subquery count: its `as_sql` selects the aggregates only). Django reads it for MULTI only.
    if isinstance(compiler, SQLAggregateCompiler):
        return False
    return bool(compiler.has_extra_select)


def from_db_callable(model_cls: Any, fetch_mode: Any) -> Any:
    return _get_from_db(model_cls, fetch_mode)


def related_populators(klass_info_: Any, select_: Any, db: str, fetch_mode: Any) -> list[Any]:
    return get_related_populators(klass_info_, select_, db, fetch_mode)


def set_peers(instance: Any, peers: list[Any]) -> None:
    instance._state.peers = peers


def get_clone(queryset: Any, args: tuple[Any, ...], kwargs: dict[str, Any]) -> Any:
    """The QuerySet ``QuerySet.get`` evaluates, before the limit (Django 6.1, up to ``num = len(clone)``)."""
    query = queryset.query
    if query.combinator and (args or kwargs):
        raise NotSupportedError(
            f"Calling QuerySet.get(...) with filters after {query.combinator}() is not supported."
        )
    # `get()` calls `self._chain()` here, not `all()`
    clone = (
        queryset._chain()
        if query.combinator
        else _own_clone(queryset.filter(*args, **kwargs), queryset)
    )
    if query.can_filter() and not query.distinct_fields:
        clone = clone.order_by()
    return raise_on_fetch(clone)


def _own_clone(clone: Any, shared: Any) -> Any:
    """``clone``, or a ``_chain()`` of ``shared`` if a QuerySet subclass returned ``shared`` itself:
    the engine mutates its clones and must never touch the wrapper's own QuerySet."""
    return shared._chain() if clone is shared else clone


def supports_select_for_update_with_limit(wrapper: Any) -> bool:
    """``connections[alias].features.supports_select_for_update_with_limit`` of the compile-only
    ``wrapper`` (it carries the real server's features once the alias is open)."""
    return bool(wrapper.features.supports_select_for_update_with_limit)


def select_for_update(queryset: Any) -> bool:
    return bool(queryset.query.select_for_update)


def limit_results(queryset: Any, high: int) -> None:
    """``clone.query.set_limits(high=high)``: ``get()`` mutates its own clone."""
    queryset.query.set_limits(high=high)


def first_queryset(queryset: Any) -> Any:
    """The QuerySet ``QuerySet.first`` slices: ordered by ``pk`` when it has no ordering."""
    if queryset.ordered or not queryset.query.default_ordering:
        return queryset
    queryset._check_ordering_first_last_queryset_aggregation(method="first")
    return raise_on_fetch(_own_clone(queryset.order_by("pk"), queryset))
