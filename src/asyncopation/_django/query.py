"""Accessors for the QuerySet and compiler internals the evaluation engine reads (ADR-0002, 0007).

Every private attribute and non-public symbol the engine needs goes through here, so a Django
upgrade breaks in one place. Nothing in this module runs I/O: the compiler is only asked about
state that ``as_sql()`` has already computed.
"""

from __future__ import annotations

from enum import Enum
from typing import Any

from django.db.models.query import (
    FlatValuesListIterable,
    ModelIterable,
    NamedValuesListIterable,
    ValuesIterable,
    ValuesListIterable,
    _get_from_db,  # ty: ignore[missing-module-member] - missing from django-types
    get_related_populators,  # ty: ignore[missing-module-member] - missing from django-types
)
from django.db.models.utils import create_namedtuple_class  # ty: ignore[missing-module-member] - missing from django-types
from django.utils.connection import ConnectionDoesNotExist

__all__ = [
    "ConnectionDoesNotExist",
    "IterableKind",
    "annotation_col_map",
    "cached_result",
    "col_count",
    "from_db_callable",
    "has_extra_select",
    "has_prefetch",
    "iterable_kind",
    "klass_info",
    "known_related_objects",
    "namedtuple_class",
    "queryset_fetch_mode",
    "queryset_fields",
    "related_populators",
    "select",
    "set_cached_result",
    "set_peers",
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


def queryset_fields(queryset: Any) -> Any:
    return queryset._fields


def known_related_objects(queryset: Any) -> Any:
    return queryset._known_related_objects


def values_names(queryset: Any) -> list[str]:
    """The key names of a ``values()`` row (``ValuesIterable.__iter__``, Django 6.1)."""
    query = queryset.query
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
    return bool(compiler.has_extra_select)


def from_db_callable(model_cls: Any, fetch_mode: Any) -> Any:
    return _get_from_db(model_cls, fetch_mode)


def related_populators(klass_info_: Any, select_: Any, db: str, fetch_mode: Any) -> list[Any]:
    return get_related_populators(klass_info_, select_, db, fetch_mode)


def set_peers(instance: Any, peers: list[Any]) -> None:
    instance._state.peers = peers
