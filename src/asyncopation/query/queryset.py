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
from collections.abc import AsyncIterator
from typing import Any

from django.db.models import FETCH_RAISE, Manager, QuerySet  # ty: ignore[missing-module-member] - missing from django-types

from asyncopation._django import query as dq
from asyncopation.engine.evaluate import evaluate_list
from asyncopation.engine.read import evaluate_count, evaluate_exists, evaluate_first, evaluate_get
from asyncopation.errors import SyncEvaluationError

__all__ = [
    "AQ_MANAGER_HOOK",
    "AsyncQuerySet",
    "aq",
    "manager_queryset",
    "refuse_blocked",
    "wrap_result",
]

# The method a manager-level object (`AsyncObjects`, ADR-0009 Decision 7) defines on its type to
# hand `aq()` its bound manager; `query/objects.py` imports this module, not the other way round.
AQ_MANAGER_HOOK = "_asyncopation_aq_manager"

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

    # `_fresh`: the QuerySet came straight from a manager, so `all()` behaves as `Manager.all()`.
    # `_prefetched`: the cache was already filled when `aq()` wrapped it (a prefetched related
    # manager), so `all()` may share it; a cache `alist()` filled later is never shared.
    # Invariant: `_prefetched` implies `_fresh` (only `aq()` sets it, always with `fresh=True`).
    __slots__ = ("_fresh", "_prefetched", "_queryset")

    def __init__(self, queryset: Any, *, fresh: bool = False, prefetched: bool = False) -> None:
        self._queryset = queryset
        self._fresh = fresh
        self._prefetched = prefetched

    @property
    def queryset(self) -> Any:
        """The wrapped Django QuerySet (unwrap before passing it to code that type-checks it)."""
        return self._queryset

    # -- evaluation -------------------------------------------------------------------------
    async def alist(self) -> list[Any]:
        """Evaluate the QuerySet and return the rows as a list.

        Fills the result cache of the wrapped QuerySet (``aq()`` cloned it, so the one you passed in
        is untouched; a manager's own fresh QuerySet is the exception, nobody else holds it); a
        second call returns the cache's rows without I/O, as Django's does. The list is a copy:
        changing it never changes the cache.
        """
        return await evaluate_list(self._queryset)

    async def aget(self, *args: Any, **kwargs: Any) -> Any:
        """The one object matching the arguments; ``DoesNotExist`` / ``MultipleObjectsReturned`` as Django's ``get()``."""
        return await evaluate_get(self._queryset, args, kwargs)

    async def afirst(self) -> Any:
        """The first object (ordered by ``pk`` when the QuerySet has no ordering), or ``None``."""
        return await evaluate_first(self._queryset)

    async def acount(self) -> int:
        """The number of rows: the result cache's length when it is filled, else ``COUNT(*)``."""
        return await evaluate_count(self._queryset)

    async def aexists(self) -> bool:
        """Whether the QuerySet has any row (a ``LIMIT 1`` query unless the result cache is filled)."""
        return await evaluate_exists(self._queryset)

    async def __aiter__(self) -> AsyncIterator[Any]:
        """Iterate the rows: evaluates like :meth:`alist` (filling the result cache) first."""
        for obj in await self.alist():
            yield obj

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
            return _wrap(
                self._queryset[key],
                own_method=type(self._queryset).__getitem__ in _DJANGO_FUNCTIONS,
            )
        raise _evaluation_error("indexing an aq() QuerySet", "aget")

    def __reduce__(self) -> Any:
        # QuerySet.__getstate__ evaluates the QuerySet (blocking I/O), so pickling is refused.
        raise _evaluation_error("pickling an aq() QuerySet")

    def all(self) -> AsyncQuerySet:
        """A new wrapper over a clone. Straight from a manager it behaves as ``Manager.all()``,
        which returns ``get_queryset()`` unchained: the pending sticky filter is kept, and a
        prefetched cache is returned as it is (``aq(t.books).all().alist()`` makes no statement),
        but only once: the result is an ordinary wrapper, so its own ``all()`` is a new unevaluated
        QuerySet, as Django's ``t.books.all().all()`` is. A cache that ``alist()`` filled is not
        shared: ``all()`` is always a new unevaluated QuerySet.

        ``copy.copy`` differs on purpose: it is an unevaluated clone that drops a prefetched cache,
        so a copy of a prefetched wrapper queries where its ``all()`` reads the cache."""
        if self._prefetched:
            return AsyncQuerySet(self._queryset)
        wrapped = _wrap(
            self._queryset.all(), own_method=type(self._queryset).all in _DJANGO_FUNCTIONS
        )
        if self._fresh:
            dq.copy_filter_sticky(wrapped.queryset, self._queryset)
        return wrapped

    def __copy__(self) -> AsyncQuerySet:
        # A clone (copy.copy would evaluate the QuerySet) that keeps FETCH_RAISE and the pending
        # sticky filter, like the wrapper it copies.
        return AsyncQuerySet(_raise_clone(self._queryset), fresh=self._fresh)

    def __deepcopy__(self, memo: dict[int, Any]) -> AsyncQuerySet:
        return AsyncQuerySet(_wrap(copy.deepcopy(self._queryset, memo)).queryset, fresh=self._fresh)

    # -- combining ----------------------------------------------------------------------------
    def __or__(self, other: Any) -> AsyncQuerySet:
        return _wrap(
            self._queryset | _unwrap(other),
            self,
            other,
            own_method=type(self._queryset).__or__ in _DJANGO_FUNCTIONS,
        )

    def __and__(self, other: Any) -> AsyncQuerySet:
        return _wrap(
            self._queryset & _unwrap(other),
            self,
            other,
            own_method=type(self._queryset).__and__ in _DJANGO_FUNCTIONS,
        )

    def __xor__(self, other: Any) -> AsyncQuerySet:
        return _wrap(
            self._queryset ^ _unwrap(other),
            self,
            other,
            own_method=type(self._queryset).__xor__ in _DJANGO_FUNCTIONS,
        )

    def __repr__(self) -> str:
        # QuerySet.__repr__ evaluates self[:21]; this one never touches the database.
        return f"<AsyncQuerySet of {self._queryset.model._meta.label} using {self._queryset.db!r}>"

    # -- proxying -----------------------------------------------------------------------------
    def __getattr__(self, name: str) -> Any:
        if name == "_queryset" or (name.startswith("__") and name.endswith("__")):
            # Not proxied: protocol lookups (copy, pickle) and an instance without its slot yet
            # must not recurse into the QuerySet.
            raise AttributeError(name)
        refuse_blocked(name)
        attribute = getattr(self._queryset, name)
        if callable(attribute) and not isinstance(attribute, type):
            return _proxy(attribute, self)
        return attribute


def refuse_blocked(name: str) -> None:
    """Raise for a name of the blocking tables (ADR-0006 Decision 2), also used by ``aobjects``
    (ADR-0009 Decision 3): sync evaluators, Django's coroutines that are not implemented and
    ``raw``."""
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


# The functions Django defines on QuerySet: a method (a bound method's function, or the class
# attribute for an operator, all() or slicing) that is one of them is not an override or a
# user-defined method.
_DJANGO_FUNCTIONS = frozenset(v for v in vars(QuerySet).values() if inspect.isfunction(v))


def _proxy(method: Any, owner: AsyncQuerySet) -> Any:
    # Only Django's own methods are known to return a fresh clone; a user-defined one (an override
    # included) may return any QuerySet, such as one it stores.
    own = getattr(method, "__func__", None) in _DJANGO_FUNCTIONS

    @functools.wraps(method)
    def call(*args: Any, **kwargs: Any) -> Any:
        # Django never receives a wrapper: ``none().union(aq_qs)`` returns the argument itself, and
        # it would hand the wrapper back as if it were a QuerySet.
        result = method(
            *(_unwrap(value) for value in args),
            **{key: _unwrap(value) for key, value in kwargs.items()},
        )
        return _wrap(result, owner, *args, *kwargs.values(), own_method=own)

    return call


def _unwrap(value: Any) -> Any:
    return value._queryset if isinstance(value, AsyncQuerySet) else value


def _wrap(value: Any, *operands: Any, own_method: bool = True) -> Any:
    """Re-wrap what a proxied method returned. Django's chaining methods hand back a fresh clone,
    so a ``FETCH_RAISE`` result is wrapped as it is; any other QuerySet is cloned into that mode,
    and so is anything a user-defined method (``own_method=False``) returned: it may be a QuerySet
    that the method stores.

    A result that IS one of ``operands`` (wrappers or QuerySets: the receiver or an argument) is
    cloned whatever its mode: ``none() | qs`` and ``none().union(qs)`` return ``qs`` itself, and a
    user-defined method may return its receiver or an argument, so wrapping it as it is would let
    ``alist()`` fill a QuerySet someone else holds. The one exception is a result whose cache is
    already filled (a prefetched related manager, a wrapper that ``alist()`` filled, a stored
    QuerySet a user method returns): Django returns that same QuerySet and evaluating it makes no
    statement (``cached | none() is cached``), so the result shares it; ``alist()`` only reads that
    cache. The result is an ordinary wrapper, so its own ``all()`` re-queries as Django's does."""
    if not isinstance(value, QuerySet):
        return value
    operands_held = [operand for operand in operands if value is _unwrap(operand)]
    if dq.cached_result(value) is not None:
        return AsyncQuerySet(value)
    if operands_held or not own_method or dq.queryset_fetch_mode(value) is not FETCH_RAISE:
        return AsyncQuerySet(_raise_clone(value))
    return AsyncQuerySet(value)


def _raise_clone(queryset: Any) -> Any:
    """A ``FETCH_RAISE`` clone of ``queryset`` that keeps its pending sticky-filter state.

    ``QuerySet.fetch_mode()`` is a chain, which consumes ``query.filter_is_sticky``; ``t.books.all()``
    followed by ``.filter(tags__name=x)`` must behave as it does without the clone.
    """
    clone = queryset.fetch_mode(FETCH_RAISE)
    dq.copy_filter_sticky(clone, queryset)
    return clone


def aq(obj: Any) -> AsyncQuerySet:
    """Wrap a QuerySet or Manager (related managers included) for asynchronous evaluation.

    ``aq(aq(x))`` is ``aq(x)``. The QuerySet gets ``FETCH_RAISE`` so attribute access never does
    synchronous I/O. A real ``Manager``'s own fresh ``get_queryset()`` result is switched in place,
    because ``QuerySet.fetch_mode()`` would clone it and a clone consumes the pending sticky filter
    of a many-to-many related manager (``aq(tag.books).filter(tags__name=x)`` would then differ
    from Django's). This assumes ``Manager.get_queryset()`` returns a fresh QuerySet, as Django's
    and related managers do; one that is already ``FETCH_RAISE`` may be shared and is cloned.
    Everything else (a QuerySet you pass, or what a duck-typed ``get_queryset()`` returns) may be
    shared, so it is cloned and never touched; the clone keeps the sticky state. The one exception
    is a QuerySet whose result cache is filled (a related manager after ``prefetch_related``): it is
    wrapped as it is, because ``alist()`` only reads that cache.
    """
    if isinstance(obj, AsyncQuerySet):
        return obj
    if isinstance(obj, Manager):
        return manager_queryset(obj.get_queryset())
    # The manager-level `aobjects` object (ADR-0009 Decision 7), found on its type: its instance
    # `__getattr__` proxies a manager and must not be asked.
    bound_manager = getattr(type(obj), AQ_MANAGER_HOOK, None)
    if bound_manager is not None:
        return aq(bound_manager(obj))
    from_source = not isinstance(obj, QuerySet) and hasattr(obj, "get_queryset")
    queryset = obj.get_queryset() if from_source else obj
    _require_queryset(queryset)
    if dq.cached_result(queryset) is not None:
        return AsyncQuerySet(queryset)
    return AsyncQuerySet(_raise_clone(queryset))


def manager_queryset(queryset: Any) -> AsyncQuerySet:
    """Wrap a real ``Manager``'s own fresh ``get_queryset()`` result (or Django's ``all()``, which
    returns it unchained): ``FETCH_RAISE`` is set in place (ADR-0006 Decision 4); a QuerySet that is
    already ``FETCH_RAISE`` may be shared and is cloned; a filled cache is wrapped as it is."""
    _require_queryset(queryset)
    if dq.cached_result(queryset) is not None:
        return AsyncQuerySet(queryset, fresh=True, prefetched=True)
    if dq.queryset_fetch_mode(queryset) is FETCH_RAISE:
        return AsyncQuerySet(_raise_clone(queryset), fresh=True)
    dq.set_fetch_mode(queryset, FETCH_RAISE)
    return AsyncQuerySet(queryset, fresh=True)


def wrap_result(value: Any) -> Any:
    """Re-wrap what a user-defined method returned (ADR-0006 Decision 4): a QuerySet is cloned into
    ``FETCH_RAISE`` unless its cache is filled; anything else is returned as it is."""
    return _wrap(value, own_method=False)


def _require_queryset(value: Any) -> None:
    if not isinstance(value, QuerySet):
        raise TypeError(f"aq() takes a QuerySet or a Manager, not {type(value).__name__}")
