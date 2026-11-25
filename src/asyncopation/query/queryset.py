"""``aq()`` and the ``AsyncQuerySet`` composition wrapper (ADR-0006 Decisions 1, 2 and 4).

QuerySet building stays Django's: chaining methods are proxied and every QuerySet they return is
wrapped again, so user-defined QuerySet methods keep working. Evaluation is Asyncopation's own. A
coroutine method Django defines on ``QuerySet`` that is not implemented here yet raises
``NotImplementedError`` and never falls through to Django's ``sync_to_async`` version, and the
synchronous evaluators raise ``SyncEvaluationError`` instead of blocking the event loop.
"""

from __future__ import annotations

import functools
import inspect
from typing import Any

from django.db.models import FETCH_RAISE, Manager, QuerySet  # ty: ignore[missing-module-member] - missing from django-types

from asyncopation._django import query as dq
from asyncopation.engine.evaluate import evaluate_list
from asyncopation.errors import SyncEvaluationError

__all__ = ["AsyncQuerySet", "aq"]

# Django's own coroutine methods (alist-like evaluators, aget, acreate, ...): ours replace them one by
# one; until then they must not reach `sync_to_async`. The scan is over the class, so a Django
# upgrade that adds one is blocked too.
_DJANGO_COROUTINES = frozenset(
    name for name, value in vars(QuerySet).items() if inspect.iscoroutinefunction(value)
)

# Synchronous evaluators (ADR-0006 Decision 2) -> the coroutine to await instead.
_SYNC_EVALUATORS = {
    "get": "aget",
    "first": "afirst",
    "last": "alast",
    "earliest": "aearliest",
    "latest": "alatest",
    "count": "acount",
    "exists": "aexists",
    "contains": "acontains",
    "iterator": "aiterator",
    "aggregate": "aaggregate",
    "update": "aupdate",
    "delete": "adelete",
    "create": "acreate",
    "get_or_create": "aget_or_create",
    "update_or_create": "aupdate_or_create",
    "bulk_create": "abulk_create",
    "bulk_update": "abulk_update",
    "in_bulk": "ain_bulk",
    "explain": "aexplain",
}


def _evaluation_error(name: str, hint: str | None = None) -> SyncEvaluationError:
    use = f"; await {hint}() instead" if hint else ""
    return SyncEvaluationError(f"{name} would run a blocking database query on the event loop{use}")


class AsyncQuerySet:
    """Wraps a Django QuerySet; created by :func:`aq`, never by hand."""

    __slots__ = ("_queryset",)

    def __init__(self, queryset: Any) -> None:
        self._queryset = queryset

    @property
    def queryset(self) -> Any:
        """The wrapped Django QuerySet (unwrap before passing it to code that type-checks it)."""
        return self._queryset

    # -- evaluation -------------------------------------------------------------------------
    async def alist(self) -> list[Any]:
        """Evaluate the QuerySet and return the rows as a list (fills its result cache)."""
        return await evaluate_list(self._queryset)

    def __aiter__(self) -> Any:
        raise NotImplementedError("async iteration is not implemented yet (#6, PR B)")

    # -- synchronous evaluators are refused ---------------------------------------------------
    def __iter__(self) -> Any:
        raise _evaluation_error("iterating an aq() QuerySet", "alist")

    def __len__(self) -> int:
        raise _evaluation_error("len() of an aq() QuerySet", "acount")

    def __bool__(self) -> bool:
        raise _evaluation_error("bool() of an aq() QuerySet", "aexists")

    def __getitem__(self, key: Any) -> Any:
        if isinstance(key, slice):
            return _wrap(self._queryset[key])
        raise _evaluation_error("indexing an aq() QuerySet", "aget")

    def __repr__(self) -> str:
        # QuerySet.__repr__ evaluates self[:21]; this one never touches the database.
        return f"<AsyncQuerySet of {self._queryset.model._meta.label} using {self._queryset.db!r}>"

    # -- proxying -----------------------------------------------------------------------------
    def __getattr__(self, name: str) -> Any:
        if name in _SYNC_EVALUATORS:
            raise _evaluation_error(f"QuerySet.{name}()", _SYNC_EVALUATORS[name])
        if name in _DJANGO_COROUTINES:
            raise NotImplementedError(f"AsyncQuerySet.{name}() is not implemented yet")
        attribute = getattr(self._queryset, name)
        if callable(attribute) and not isinstance(attribute, type):
            return _proxy(attribute)
        return attribute


def _proxy(method: Any) -> Any:
    @functools.wraps(method)
    def call(*args: Any, **kwargs: Any) -> Any:
        return _wrap(method(*args, **kwargs))

    return call


def _wrap(value: Any) -> Any:
    return aq(value) if isinstance(value, QuerySet) else value


def aq(obj: Any) -> AsyncQuerySet:
    """Wrap a QuerySet or Manager (related managers included) for asynchronous evaluation.

    ``aq(aq(x))`` is ``aq(x)``. The QuerySet gets ``FETCH_RAISE`` so attribute access never does
    synchronous I/O; one that already carries a result cache (a related manager after
    ``prefetch_related``) is wrapped as it is, because cloning would drop the cache.
    """
    if isinstance(obj, AsyncQuerySet):
        return obj
    if isinstance(obj, Manager) or (not isinstance(obj, QuerySet) and hasattr(obj, "get_queryset")):
        obj = obj.get_queryset()
    if not isinstance(obj, QuerySet):
        raise TypeError(f"aq() takes a QuerySet or a Manager, not {type(obj).__name__}")
    if dq.cached_result(obj) is None:
        obj = obj.fetch_mode(FETCH_RAISE)
    return AsyncQuerySet(obj)
