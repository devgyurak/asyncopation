"""Throwaway managers, models and `AsyncObjects` subclasses for the manager-level `aobjects` tests
(ADR-0009), in private app registries (no tables, no effect on the harness catalog)."""

from __future__ import annotations

import asyncio
import contextlib
import functools
import gc
import itertools
import threading
import warnings
from collections.abc import Callable, Generator
from dataclasses import dataclass
from typing import Any
from unittest import mock

from asgiref.sync import sync_to_async
from django.apps.registry import Apps
from django.contrib.auth.base_user import AbstractBaseUser, BaseUserManager
from django.db import models
from django.db.models.manager import BaseManager

from asyncopation import AsyncObjects
from asyncopation.query.objects import EVALUATORS
from tests.support.aio import collect
from tests.support.object_models import Plain

registry = Apps()
LABEL = "aobjects_manager_unit"


def _meta(**options: Any) -> type:
    return type("Meta", (), {"app_label": LABEL, "apps": registry, **options})


# Objects the managers below hand out, filled per test by fixtures (never shared between tests).
STORED: dict[str, Any] = {}


# -- QuerySets -------------------------------------------------------------------------------------


class ItemQuerySet(models.QuerySet):
    def cheap(self) -> Any:
        return self.filter(price__lt=10)

    def first_name(self) -> Any:
        """Evaluates internally (a sync query inside a QuerySet method)."""
        first: Any = self.first()
        return first.name


class RichItemQuerySet(ItemQuerySet):
    """The second QuerySet of a chained `from_queryset`; it subclasses the first so the first's
    copies keep working on it, as Django requires."""

    def expensive(self) -> Any:
        return self.filter(price__gte=100)


# -- managers --------------------------------------------------------------------------------------

ItemManager: Any = models.Manager.from_queryset(ItemQuerySet)


class PublishedManager(ItemManager):
    """Manager-only names next to the copies of `ItemQuerySet`."""

    def published(self) -> Any:
        return self.get_queryset().filter(active=True)

    def raw_all(self) -> Any:
        return self.raw("SELECT 1")

    def label(self) -> str:
        return "manager label"

    def other_manager(self) -> Any:
        """The default manager of another model."""
        return Plain._default_manager

    def loose_manager(self) -> Any:
        """A manager that was never attached to a model (its ``model`` is None)."""
        return models.Manager()


class AliasManager(models.Manager):
    """`published` is Django's copy of `filter` under another name (it keeps `__name__ == "filter"`)."""

    published = models.Manager.filter


class FilterOverrideManager(models.Manager):
    """Overrides the copied `filter` to add a condition."""

    def filter(self, *args: Any, **kwargs: Any) -> Any:
        return super().filter(*args, **kwargs).filter(active=True)


def _adds_active(method: Callable[..., Any]) -> Callable[..., Any]:
    @functools.wraps(method)
    def decorated(self: Any, *args: Any, **kwargs: Any) -> Any:
        return method(self, *args, **kwargs).filter(active=True)

    return decorated


class DecoratedManager(models.Manager):
    """Django's `filter` copy wrapped by a `functools.wraps` decorator that adds a condition."""

    filter = _adds_active(models.Manager.filter)


ChainedManager: Any = models.Manager.from_queryset(ItemQuerySet).from_queryset(RichItemQuerySet)

# A default manager that is a `BaseManager` but not a `Manager`.
BaseOnlyManager: Any = BaseManager.from_queryset(models.QuerySet)


def _manager_get(self: Any, *args: Any, **kwargs: Any) -> str:
    return "manager get"


def _manager_count(self: Any) -> int:
    return 0


def _manager_acreate(self: Any, **kwargs: Any) -> str:
    return "manager acreate"


def _manager_aiterator(self: Any) -> str:
    return "manager aiterator"


def _manager_raw(self: Any, *args: Any, **kwargs: Any) -> str:
    return "manager raw"


# Defines names that the blocking tables refuse (sync methods, so a proxied call would succeed);
# built with type() because the overrides deliberately break Django's signatures.
BlockingNamesManager: Any = type(
    "BlockingNamesManager",
    (models.Manager,),
    {
        "__module__": __name__,
        "get": _manager_get,
        "count": _manager_count,
        "acreate": _manager_acreate,
        "aiterator": _manager_aiterator,
        "raw": _manager_raw,
    },
)


def _one() -> int:
    return 1


class AsyncNamesManager(models.Manager):
    """Coroutines, async generators and sync methods handing them out, all on the manager."""

    async def anative(self) -> int:
        return 1

    async def astream(self) -> Any:
        yield 1

    asynced = sync_to_async(_one)

    def acount_active(self) -> Any:
        return self.get_queryset().filter(active=True).acount()

    def stream(self) -> Any:
        return self.get_queryset().aiterator()

    def future(self) -> Any:
        """A pending `asyncio.Future` (needs a running loop)."""
        STORED["future"] = asyncio.get_running_loop().create_future()
        return STORED["future"]

    def task(self) -> Any:
        """A pending `asyncio.Task` that would never finish on its own (needs a running loop)."""
        STORED["future"] = asyncio.ensure_future(asyncio.Event().wait())
        return STORED["future"]


class StoredResultManager(models.Manager):
    """Manager-defined methods returning QuerySets that the test (not Django) holds."""

    def shared(self) -> Any:
        return STORED["shared"]

    def filled(self) -> Any:
        return STORED["filled"]


GET_QUERYSET_CALLS: list[int] = []


class CountingManager(models.Manager):
    """Records every `get_queryset()` call in `GET_QUERYSET_CALLS`."""

    def get_queryset(self) -> Any:
        GET_QUERYSET_CALLS.append(1)
        return super().get_queryset()


# -- AsyncObjects subclasses -----------------------------------------------------------------------


class ModelAsyncObjects(AsyncObjects):
    """An async manager method that reads the bound model."""

    async def amodel(self) -> Any:
        return self.model


class MarkingAsyncObjects(AsyncObjects):
    """Overrides the `aget` evaluator and reaches the default through `super()`."""

    async def aget(self, *args: Any, **kwargs: Any) -> Any:
        return ("override", await super().aget(*args, **kwargs))


class PassThroughAsyncObjects(AsyncObjects):
    """Overrides `aget` with a pass-through to the default that records each call in a list set
    by `__init__` (a bound object carries the declared object's instance state over)."""

    def __init__(self) -> None:
        super().__init__()
        self.calls: list[str] = []

    async def aget(self, *args: Any, **kwargs: Any) -> Any:
        self.calls.append("aget")
        return await super().aget(*args, **kwargs)


class SlottedAsyncObjects(AsyncObjects):
    """Keeps `__init__` state in slots, not in `__dict__`: a private (mangled) one and a plain one."""

    __slots__ = ("__secret", "plain")

    def __init__(self) -> None:
        super().__init__()
        self.__secret = "hidden"
        self.plain = "tagged"

    def secret(self) -> str:
        return self.__secret


class SilentHookAsyncObjects(AsyncObjects):
    """Overrides `__init_subclass__` without calling `super()` (so `AsyncObjects` never sees its
    subclasses)."""

    def __init_subclass__(cls, **kwargs: Any) -> None:
        pass


class SlottedBelowSilentHook(SilentHookAsyncObjects):
    """Declares a slot below a class whose `__init_subclass__` skips `AsyncObjects`'."""

    __slots__ = ("depth",)

    def __init__(self) -> None:
        super().__init__()
        self.depth = "below"


class FaultyPropertyAsyncObjects(AsyncObjects):
    """Properties whose body raises `AttributeError`: the manager has a `label` of its own, but no
    `missing`."""

    @property
    def label(self) -> str:
        raise AttributeError("internal failure")

    @property
    def missing(self) -> str:
        raise AttributeError("internal failure")


class DbPropertyAsyncObjects(AsyncObjects):
    """A property over the manager's `db`, reaching it through `super().__getattr__`."""

    @property
    def db(self) -> str:
        return "via:" + super().__getattr__("db")


class OwnNamesAsyncObjects(AsyncObjects):
    """Defines a blocking-table name (`get`) and a manager name (`published`) itself."""

    def get(self) -> str:
        return "own get"

    def published(self) -> str:
        return "own published"


class DelegatingAsyncObjects(AsyncObjects):
    """Overrides a proxied name and an evaluator that does not exist yet, reaching both via `super()`."""

    def published(self) -> Any:
        return super().__getattr__("published")()

    async def acreate(self, **kwargs: Any) -> Any:
        return await super().acreate(**kwargs)  # ty: ignore[missing-attribute]


def define_subclass_with(attribute: str) -> type:
    """Create an `AsyncObjects` subclass that defines `attribute` itself."""
    return type("Shadowing", (AsyncObjects,), {attribute: None})


def define_mixin_subclass_with(attribute: str) -> type:
    """Create an `AsyncObjects` subclass whose mixin (first in the MRO) defines `attribute`."""
    mixin = type("ShadowingMixin", (), {attribute: None})
    return type("MixedIn", (mixin, AsyncObjects), {})


# -- models ----------------------------------------------------------------------------------------


class Fields(models.Model):
    name = models.CharField(max_length=10)
    price = models.IntegerField(default=0)
    active = models.BooleanField(default=True)

    Meta = _meta(abstract=True)

    def __str__(self) -> str:
        return type(self).__name__


class Item(Fields):
    objects = PublishedManager()
    aobjects = AsyncObjects()

    Meta = _meta()


class AliasItem(Fields):
    objects = AliasManager()
    aobjects = AsyncObjects()

    Meta = _meta()


class FilterItem(Fields):
    objects = FilterOverrideManager()
    aobjects = AsyncObjects()

    Meta = _meta()


class DecoratedItem(Fields):
    objects = DecoratedManager()
    aobjects = AsyncObjects()

    Meta = _meta()


class ChainedItem(Fields):
    objects = ChainedManager()
    aobjects = AsyncObjects()

    Meta = _meta()


class BlockedItem(Fields):
    objects = BlockingNamesManager()
    aobjects = AsyncObjects()

    Meta = _meta()


class AsyncItem(Fields):
    objects = AsyncNamesManager()
    aobjects = AsyncObjects()

    Meta = _meta()


class StoredItem(Fields):
    objects = StoredResultManager()
    aobjects = AsyncObjects()

    Meta = _meta()


class CountingItem(Fields):
    objects = CountingManager()
    aobjects = AsyncObjects()

    Meta = _meta()


class BaseManagedItem(Fields):
    objects = BaseOnlyManager()
    aobjects = ModelAsyncObjects()

    Meta = _meta()


class MarkingItem(Fields):
    aobjects = MarkingAsyncObjects()

    Meta = _meta()


class OwnedItem(Fields):
    objects = PublishedManager()
    aobjects = OwnNamesAsyncObjects()

    Meta = _meta()


class DelegatingItem(Fields):
    objects = PublishedManager()
    aobjects = DelegatingAsyncObjects()

    Meta = _meta()


class StatefulItem(Fields):
    aobjects = PassThroughAsyncObjects()

    Meta = _meta()


class SlottedItem(Fields):
    aobjects = SlottedAsyncObjects()

    Meta = _meta()


class SilentHookItem(Fields):
    aobjects = SlottedBelowSilentHook()

    Meta = _meta()


class FaultyItem(Fields):
    objects = PublishedManager()
    aobjects = FaultyPropertyAsyncObjects()

    Meta = _meta()


class DbItem(Fields):
    aobjects = DbPropertyAsyncObjects()

    Meta = _meta()


class Bare(Fields):
    """No `aobjects`: `attach` is pointed at it."""

    Meta = _meta()


class Root(Fields):
    aobjects = ModelAsyncObjects()

    Meta = _meta()


class RootChild(Root):
    extra = models.IntegerField(default=0)

    Meta = _meta()


class RootProxy(Root):
    Meta = _meta(proxy=True)


class AbstractRoot(Fields):
    aobjects = ModelAsyncObjects()

    Meta = _meta(abstract=True)


class AbstractRootChild(AbstractRoot):
    Meta = _meta()


class Member(AbstractBaseUser):
    """A user model with Django's own `BaseUserManager` (`aget_by_natural_key` is a coroutine it
    defines, `get_by_natural_key` a sync method that queries). `django.contrib.auth` is not
    installed in the harness, so `auth.User` / `UserManager` cannot be imported here."""

    username = models.CharField(max_length=20, unique=True)
    objects = BaseUserManager()

    USERNAME_FIELD = "username"

    Meta = _meta()

    def __str__(self) -> str:
        return self.username


def attach_to_member() -> type:
    AsyncObjects.attach(Member)
    return Member


def detach_from_member() -> None:
    delattr(Member, "aobjects")


def attach_subclass_to_bare() -> type:
    ModelAsyncObjects.attach(Bare)
    return Bare


def detach_from_bare() -> None:
    delattr(Bare, "aobjects")


def attach_pass_through(model: Any) -> Any:
    """`PassThroughAsyncObjects.attach(model)`; returns the declared object it replaced."""
    original = vars(model)["aobjects"]
    PassThroughAsyncObjects.attach(model)
    return original


def restore_aobjects(model: Any, original: Any) -> None:
    model.aobjects = original


def pass_through_calls(model: Any) -> list[str]:
    """The calls recorded by the `PassThroughAsyncObjects` declared on `model`."""
    return list(vars(model)["aobjects"].calls)


# -- models declaring a subclass that reuses a private name of AsyncObjects ------------------------

shadow_registry = Apps()
_shadow_numbers = itertools.count()


def model_declaring_subclass_with(attribute: str) -> Any:
    """A model (private registry, unique name) whose `aobjects` is an `AsyncObjects` subclass that
    defines `attribute` (set to None) itself."""
    subclass = define_subclass_with(attribute)
    number = next(_shadow_numbers)
    return type(
        f"ShadowItem{number}",
        (models.Model,),
        {
            "__module__": __name__,
            "__str__": _receiver_str,
            "Meta": type("Meta", (), {"app_label": "aobjects_shadow", "apps": shadow_registry}),
            "name": models.CharField(max_length=10),
            "aobjects": subclass(),
        },
    )


# -- stored QuerySets, coroutines and futures ------------------------------------------------------


def store_querysets() -> dict[str, Any]:
    """Fill `STORED` with a fresh unevaluated QuerySet (`shared`) and a filled-cache one (`filled`)
    of `StoredItem`, built directly (not in FETCH_RAISE) and held by the test."""
    STORED["shared"] = models.QuerySet(model=StoredItem)
    STORED["filled"] = models.QuerySet(model=StoredItem)
    STORED["filled"]._result_cache = []
    return dict(STORED)


def clear_stored() -> None:
    STORED.clear()


def queryset_fetch_mode(queryset: Any) -> Any:
    return queryset._fetch_mode


def set_instance_all(model: Any, queryset: Any) -> Any:
    """Set `all` on the INSTANCE of `model`'s default manager to return `queryset`."""
    manager = model._default_manager
    manager.all = lambda: queryset
    return manager


def unset_instance_all(manager: Any) -> None:
    del manager.all


async def _pending() -> int:
    return 1


def store_pending_coroutine(model: Any, name: str) -> Any:
    """Set a not-yet-started coroutine object as attribute `name` of `model`'s default manager."""
    coroutine = _pending()
    setattr(model._default_manager, name, coroutine)
    return coroutine


def remove_pending_coroutine(model: Any, name: str, coroutine: Any) -> None:
    coroutine.close()
    delattr(model._default_manager, name)


def is_open(coroutine: Any) -> bool:
    """A coroutine that was closed (or finished) has no frame any more."""
    return coroutine.cr_frame is not None


def handed_out_future() -> Any:
    return STORED["future"]


async def discard_handed_out() -> None:
    """Cancel and await the future a manager method handed out (if any), then forget it, so no
    pending task outlives the test even when the code under test did not cancel it."""
    future = STORED.pop("future", None)
    if future is not None:
        future.cancel()
        await asyncio.gather(future, return_exceptions=True)
    clear_stored()


async def cancelled_after_a_turn(future: Any) -> bool:
    """Let the loop process a cancellation, report it, then make sure the future is done."""
    await asyncio.wait([future], timeout=0.1)
    cancelled = future.cancelled()
    future.cancel()
    await asyncio.gather(future, return_exceptions=True)
    return cancelled


# -- reading results -------------------------------------------------------------------------------


def queryset_db(wrapper: Any) -> str:
    return wrapper.queryset.db


def declared_object(model: Any, name: str = "aobjects") -> Any:
    """The unbound object declared on the model (`vars(Model)["aobjects"]`)."""
    return vars(model)[name]


async def await_evaluator(obj: Any, name: str) -> Any:
    """`await obj.<name>()`; the attribute access, the call or the await may raise."""
    return await getattr(obj, name)()


def sliced(obj: Any) -> Any:
    return obj[:1]


def or_combined(obj: Any) -> Any:
    return obj | obj


def iterated(obj: Any) -> Any:
    return iter(obj)


async def collected(obj: Any) -> list[Any]:
    """`async for` over `obj` (raises `TypeError` when it is not async-iterable)."""
    return await collect(obj)


def shares_manager(first: Any, second: Any) -> bool:
    """Whether two bound objects hold the very same manager (the private binding slot)."""
    return first._asyncopation_manager is second._asyncopation_manager


def get_queryset_calls_during(call: Callable[[], Any]) -> int:
    """`get_queryset()` calls of a `CountingManager` while `call()` runs."""
    GET_QUERYSET_CALLS.clear()
    call()
    return len(GET_QUERYSET_CALLS)


def call_twice(function: Callable[[], Any]) -> None:
    function()
    function()


def chains_during(call: Callable[[], Any]) -> int:
    """`QuerySet._chain` calls (each one is a QuerySet clone) while `call()` runs."""
    from django.db.models.query import QuerySet

    original = QuerySet._chain
    count: list[int] = []

    def counting(self: Any, *args: Any, **kwargs: Any) -> Any:
        count.append(1)
        return original(self, *args, **kwargs)

    with mock.patch.object(QuerySet, "_chain", counting):
        call()
    return len(count)


def manager_with_instance_attribute(name: str) -> Any:
    """A plain `Manager` with `name` set in its instance `__dict__` (to Django's own copy)."""
    manager = models.Manager()
    setattr(manager, name, functools.partial(models.Manager.filter, manager))
    return manager


def aq_manager_hook() -> str:
    """`asyncopation.query.queryset.AQ_MANAGER_HOOK` (imported here, so a missing name fails one
    test, not the collection of the module)."""
    from asyncopation.query.queryset import AQ_MANAGER_HOOK

    return AQ_MANAGER_HOOK


# -- refusals --------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Refusal:
    """What one refused call did: the `NotImplementedError` message (None when nothing was
    raised), the "coroutine '...<name>' was never awaited" warnings seen once the error and its
    traceback were released and garbage collected, and how many threads it started."""

    message: str | None
    unawaited: list[str]
    thread_starts: int


@contextlib.contextmanager
def _counted_thread_starts() -> Generator[list[int]]:
    original = threading.Thread.start
    starts: list[int] = []

    def counting(self: Any) -> None:
        starts.append(1)
        original(self)

    with mock.patch.object(threading.Thread, "start", counting):
        yield starts


def refusal_of(call: Callable[[], Any], coroutine: str) -> Refusal:
    """Run `call()`, expecting `NotImplementedError` (any other error propagates). `coroutine` is
    the name of the coroutine function whose "never awaited" warning would show it was dropped
    unclosed."""
    gc.collect()  # garbage of earlier tests must not report here
    message: str | None = None
    with warnings.catch_warnings(record=True) as caught, _counted_thread_starts() as starts:
        warnings.simplefilter("always")
        try:
            call()
        except NotImplementedError as exc:
            message = str(exc)
        gc.collect()
    unawaited = [
        str(w.message)
        for w in caught
        if issubclass(w.category, RuntimeWarning)
        and "never awaited" in str(w.message)
        and coroutine in str(w.message)
    ]
    return Refusal(message, unawaited, len(starts))


async def _leaked_coroutine() -> None:
    return None


def leak_a_coroutine_and_refuse() -> None:
    """Positive control for `refusal_of`: create a coroutine, drop it unclosed, refuse."""
    _dropped = _leaked_coroutine()  # deliberately never awaited or closed
    raise NotImplementedError("refused after leaking a coroutine")


def start_a_thread_and_refuse() -> None:
    """Positive control for `refusal_of`: start (and join) one thread, then refuse."""
    thread = threading.Thread(target=_one)
    thread.start()
    thread.join()
    raise NotImplementedError("refused after starting a thread")


# -- receiving models (contribute_to_class must never be proxied) ---------------------------------

receiving = Apps()
_receiver_numbers = itertools.count()


def _receiver_meta() -> type:
    return type("Meta", (), {"app_label": "aobjects_receiving", "apps": receiving})


def _receiver_str(self: Any) -> str:
    return type(self).__name__


@dataclass(frozen=True)
class Received:
    """The source model's default manager and the receiving model's managers after the source's
    bound `aobjects` was added to the receiver."""

    source_manager_kept: bool
    source_manager_model: Any
    receiver: type
    receiver_managers: list[str]
    receiver_objects_model: Any


def _received(source: Any, before: Any, receiver: Any) -> Received:
    objects = getattr(receiver, "objects", None)
    return Received(
        source._default_manager is before,
        source._default_manager.model,
        receiver,
        [manager.name for manager in receiver._meta.managers],
        getattr(objects, "model", None),
    )


def receive_in_class_body(source: Any) -> Received:
    """Create a model whose class body holds `aobjects = source.aobjects`."""
    before = source._default_manager
    receiver = type(
        f"BodyReceiver{next(_receiver_numbers)}",
        (models.Model,),
        {
            "__module__": __name__,
            "__str__": _receiver_str,
            "Meta": _receiver_meta(),
            "aobjects": source.aobjects,
        },
    )
    return _received(source, before, receiver)


def receive_with_add_to_class(source: Any) -> Received:
    """Create a model, then `add_to_class("borrowed", source.aobjects)` on it."""
    before = source._default_manager
    receiver: Any = type(
        f"AddedReceiver{next(_receiver_numbers)}",
        (models.Model,),
        {"__module__": __name__, "__str__": _receiver_str, "Meta": _receiver_meta()},
    )
    receiver.add_to_class("borrowed", source.aobjects)
    return _received(source, before, receiver)


# -- evaluator calls (recorded on AsyncQuerySet, no database) --------------------------------------


@dataclass(frozen=True)
class EvaluatorCall:
    name: str
    queryset: Any
    args: tuple[Any, ...]
    kwargs: dict[str, Any]


def _recorder(name: str, calls: list[EvaluatorCall]) -> Callable[..., Any]:
    async def evaluator(self: Any, *args: Any, **kwargs: Any) -> Any:
        calls.append(EvaluatorCall(name, self.queryset, args, kwargs))
        return ("default", name, args, kwargs)

    return evaluator


def record_evaluators(monkeypatch: Any) -> list[EvaluatorCall]:
    """Replace the evaluators of `AsyncQuerySet` (`asyncopation.query.objects.EVALUATORS`) with
    stubs that record the QuerySet they were called on and return `("default", name, args, kwargs)`."""
    from asyncopation.query import AsyncQuerySet

    calls: list[EvaluatorCall] = []
    for name in EVALUATORS:
        monkeypatch.setattr(AsyncQuerySet, name, _recorder(name, calls))
    return calls


def call_names(calls: list[EvaluatorCall]) -> list[str]:
    return [call.name for call in calls]


def distinct_querysets(calls: list[EvaluatorCall]) -> int:
    return len({id(call.queryset) for call in calls})


async def alist_twice(bound: Any) -> None:
    await bound.alist()
    await bound.alist()


async def gather_two(bound: Any) -> None:
    await asyncio.gather(bound.acount(), bound.alist())
