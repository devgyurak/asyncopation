"""Throwaway managers, models and `AsyncObjects` subclasses for the manager-level `aobjects` tests
(ADR-0009), in private app registries (no tables, no effect on the harness catalog)."""

from __future__ import annotations

import asyncio
import functools
import gc
import itertools
import threading
import warnings
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from asgiref.sync import sync_to_async
from django.apps.registry import Apps
from django.contrib.auth.base_user import AbstractBaseUser, BaseUserManager
from django.db import models

from asyncopation import AsyncObjects
from tests.support.aio import collect

registry = Apps()
LABEL = "aobjects_manager_unit"


def _meta(**options: Any) -> type:
    return type("Meta", (), {"app_label": LABEL, "apps": registry, **options})


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


# -- reading results -------------------------------------------------------------------------------


def where_conditions(wrapper: Any) -> list[tuple[str, str]]:
    """`(field name, lookup name)` of each WHERE condition of the wrapped QuerySet, in order."""
    return [
        (node.lhs.target.name, node.lookup_name) for node in wrapper.queryset.query.where.children
    ]


def fetch_mode_of(wrapper: Any) -> Any:
    return wrapper.queryset._fetch_mode


def queryset_db(wrapper: Any) -> str:
    return wrapper.queryset.db


def declared_object(model: Any, name: str = "aobjects") -> Any:
    """The unbound object declared on the model (`vars(Model)["aobjects"]`)."""
    return vars(model)[name]


async def await_evaluator(obj: Any, name: str) -> Any:
    """`await obj.<name>()`; the attribute access, the call or the await may raise."""
    return await getattr(obj, name)()


def call_named(obj: Any, name: str, *args: Any, **kwargs: Any) -> Any:
    return getattr(obj, name)(*args, **kwargs)


def read_attribute(obj: Any, name: str) -> Any:
    return getattr(obj, name)


def sliced(obj: Any) -> Any:
    return obj[:1]


def or_combined(obj: Any) -> Any:
    return obj | obj


def iterated(obj: Any) -> Any:
    return iter(obj)


async def collected(obj: Any) -> list[Any]:
    """`async for` over `obj` (raises `TypeError` when it is not async-iterable)."""
    return await collect(obj)


# -- refusals --------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Refusal:
    """What one refused call did: the `NotImplementedError` message (None when nothing was
    raised), the `RuntimeWarning`s seen once the error and its traceback were released and garbage
    collected ("coroutine ... was never awaited"), and how many threads it started."""

    message: str | None
    runtime_warnings: list[str]
    threads_started: int


def refusal_of(call: Callable[[], Any]) -> Refusal:
    """Run `call()`, expecting `NotImplementedError` (any other error propagates)."""
    threads_before = threading.active_count()
    message: str | None = None
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        try:
            call()
        except NotImplementedError as exc:
            message = str(exc)
        gc.collect()
    found = [str(w.message) for w in caught if issubclass(w.category, RuntimeWarning)]
    return Refusal(message, found, threading.active_count() - threads_before)


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

EVALUATORS = ("alist", "aget", "afirst", "acount", "aexists")


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
    """Replace the five evaluators of `AsyncQuerySet` with stubs that record the QuerySet they
    were called on and return `("default", name, args, kwargs)`."""
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
