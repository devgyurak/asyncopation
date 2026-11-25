"""``aq()`` and the ``AsyncQuerySet`` composition wrapper (ADR-0006 Decisions 1, 2 and 4).

QuerySet building stays Django's: chaining methods are proxied and every QuerySet they return is
wrapped again, so user-defined QuerySet methods keep working. Evaluation is Asyncopation's own. A
coroutine method Django defines on ``QuerySet`` that is not implemented here yet raises
``NotImplementedError`` and never falls through to Django's ``sync_to_async`` version, and the
synchronous evaluators raise ``SyncEvaluationError`` instead of blocking the event loop.
"""

from __future__ import annotations

import copy
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
# Async generators (`aiterator`) are not coroutine functions, so both predicates are needed.
_DJANGO_COROUTINES = frozenset(
    name
    for name, value in vars(QuerySet).items()
    if inspect.iscoroutinefunction(value) or inspect.isasyncgenfunction(value)
)

# Methods that exist on Django's QuerySet, are not evaluators with an async twin and would hand
# back an object that bypasses the wrapper (`raw()` returns an unwrapped RawQuerySet).
_UNSUPPORTED = frozenset({"raw"})

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


# Evaluators whose async twin is not there yet: the advice is another coroutine that is.
_PENDING_ADVICE = {"iterator": "aiterator() is not implemented yet; await alist() instead"}


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
        """Evaluate the QuerySet and return the rows as a list.

        Fills the result cache of the wrapped QuerySet (``aq()`` cloned it unless it was already
        ``FETCH_RAISE``, in which case it is that QuerySet); a second call returns the cache's
        rows without I/O, as Django's does. The list is a copy: changing it never changes the cache.
        """
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
            if key.step:  # Django evaluates the QuerySet to apply a step
                raise _evaluation_error("slicing an aq() QuerySet with a step", "alist")
            return _wrap(self._queryset[key])
        raise _evaluation_error("indexing an aq() QuerySet", "aget")

    def __reduce__(self) -> Any:
        # QuerySet.__getstate__ evaluates the QuerySet (blocking I/O), so pickling is refused.
        raise _evaluation_error("pickling an aq() QuerySet")

    def __copy__(self) -> AsyncQuerySet:
        return aq(self._queryset.all())  # a clone (copy.copy would evaluate it), FETCH_RAISE kept

    def __deepcopy__(self, memo: dict[int, Any]) -> AsyncQuerySet:
        return aq(copy.deepcopy(self._queryset, memo))

    # -- combining ----------------------------------------------------------------------------
    def __or__(self, other: Any) -> AsyncQuerySet:
        return _wrap(self._queryset | _unwrap(other))

    def __and__(self, other: Any) -> AsyncQuerySet:
        return _wrap(self._queryset & _unwrap(other))

    def __xor__(self, other: Any) -> AsyncQuerySet:
        return _wrap(self._queryset ^ _unwrap(other))

    def __repr__(self) -> str:
        # QuerySet.__repr__ evaluates self[:21]; this one never touches the database.
        return f"<AsyncQuerySet of {self._queryset.model._meta.label} using {self._queryset.db!r}>"

    # -- proxying -----------------------------------------------------------------------------
    def __getattr__(self, name: str) -> Any:
        if name == "_queryset" or (name.startswith("__") and name.endswith("__")):
            # Not proxied: protocol lookups (copy, pickle) and an instance without its slot yet
            # must not recurse into the QuerySet.
            raise AttributeError(name)
        if name in _PENDING_ADVICE:
            raise SyncEvaluationError(
                f"QuerySet.{name}() would run a blocking database query on the event loop; "
                f"{_PENDING_ADVICE[name]}"
            )
        if name in _SYNC_EVALUATORS:
            raise _evaluation_error(f"QuerySet.{name}()", _SYNC_EVALUATORS[name])
        if name in _UNSUPPORTED:
            raise NotImplementedError(
                f"AsyncQuerySet.{name}() is not supported: it returns an unwrapped RawQuerySet"
            )
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


def _unwrap(value: Any) -> Any:
    return value._queryset if isinstance(value, AsyncQuerySet) else value


def _wrap(value: Any) -> Any:
    return aq(value) if isinstance(value, QuerySet) else value


def aq(obj: Any) -> AsyncQuerySet:
    """Wrap a QuerySet or Manager (related managers included) for asynchronous evaluation.

    ``aq(aq(x))`` is ``aq(x)``. The QuerySet gets ``FETCH_RAISE`` so attribute access never does
    synchronous I/O. A Manager's own fresh ``get_queryset()`` result is switched in place, because
    ``QuerySet.fetch_mode()`` would clone it and a clone consumes the pending sticky filter of a
    many-to-many related manager (``aq(tag.books).filter(tags__name=x)`` would then differ from
    Django's). A QuerySet that is already ``FETCH_RAISE`` or already carries a result cache (a
    related manager after ``prefetch_related``) is wrapped as it is; any other QuerySet is cloned,
    so the one you passed in is never touched.
    """
    if isinstance(obj, AsyncQuerySet):
        return obj
    from_manager = isinstance(obj, Manager) or (
        not isinstance(obj, QuerySet) and hasattr(obj, "get_queryset")
    )
    if from_manager:
        obj = obj.get_queryset()
    if not isinstance(obj, QuerySet):
        raise TypeError(f"aq() takes a QuerySet or a Manager, not {type(obj).__name__}")
    if dq.cached_result(obj) is not None or dq.queryset_fetch_mode(obj) is FETCH_RAISE:
        return AsyncQuerySet(obj)
    if from_manager:
        dq.set_fetch_mode(obj, FETCH_RAISE)
        return AsyncQuerySet(obj)
    return AsyncQuerySet(obj.fetch_mode(FETCH_RAISE))
