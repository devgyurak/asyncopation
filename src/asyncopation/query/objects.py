"""``AsyncObjects``: the ``aobjects`` descriptor and its manager-level object (ADR-0009).

A plain descriptor, not a Django ``Manager``: it defines no ``contribute_to_class``, so it never
lands in ``_meta.managers``, never becomes ``_default_manager`` and never suppresses the implicit
``objects`` (ADR-0006). ``Model.aobjects`` is a new instance of the declared ``AsyncObjects``
(sub)class bound to the accessed class and its default manager. A name on it resolves, first match
wins (ADR-0009 Decision 3):

1. ``model`` and ``name``, which a subclass cannot shadow;
2. the subclass's own attributes (async manager methods, evaluator overrides);
3. the evaluators ``alist`` / ``aget`` / ``afirst`` / ``acount`` / ``aexists``, each on a fresh
   ``aq()`` of the bound manager;
4. the blocking tables of ``query/queryset.py`` (sync evaluators, Django's coroutines, ``raw``);
5. the bound manager: a QuerySet method Django copied onto it goes straight to ``aq(manager)``, a
   name the manager defines itself is proxied and its result re-wrapped (Decision 4).

``super()`` never reaches ``__getattr__``: in a subclass it reaches only what this class defines.
No method of a Django manager runs a coroutine here (non-negotiable #1).
"""

from __future__ import annotations

import asyncio
import inspect
from typing import Any, NoReturn, Self, overload

from django.db.models import QuerySet

from asyncopation._django import managers as dm
from asyncopation.query.queryset import aq, manager_queryset, refuse_blocked, wrap_result

__all__ = ["AsyncObjects"]

# The manager-level evaluators (step 3): the single source of their names.
EVALUATORS = ("alist", "aget", "afirst", "acount", "aexists")

# Names a subclass may not shadow (ADR-0009 Decision 3, step 1). The machinery the binding and
# `aq()` rely on is name-mangled (`__manager`, `__bind`, ...), so a subclass cannot shadow it.
_PROTECTED = ("model", "name")

# Never proxied to the manager (ADR-0009 Decision 1): the binding slots, and
# `contribute_to_class`, which Django calls on any class-body or `add_to_class` value that has it.
_NOT_PROXIED = frozenset({"_AsyncObjects__model", "_AsyncObjects__manager", "contribute_to_class"})


class AsyncObjects:
    """Declare ``aobjects = AsyncObjects()`` on a model, or ``AsyncObjects.attach(Model)``.

    Subclass it to add async manager methods; they do I/O through the evaluators reached from
    ``self`` (``await self.filter(...).aget(...)``) and read the bound model as ``self.model``.
    A subclass must be constructible without arguments (``attach`` calls ``cls()``); its
    ``__init__`` runs once, and every bound object copies the declared object's ``__dict__`` and
    slots.

    Unlike Django's manager descriptor it does not refuse a swapped-out model (``AUTH_USER_MODEL``
    replacing ``auth.User``): guard A102 reserves the ``swapped`` name for ``compile/scope.py``, so
    attach it to the model that is in use.
    """

    name: str = "aobjects"
    # The binding: None on the declared (unbound) object.
    __model: Any = None
    __manager: Any = None

    def __init_subclass__(cls, **kwargs: Any) -> None:
        super().__init_subclass__(**kwargs)
        for attr in _PROTECTED:
            owner = next(klass for klass in cls.__mro__ if attr in vars(klass))
            if owner is not AsyncObjects:
                raise TypeError(
                    f"{cls.__name__} would shadow AsyncObjects.{attr} (defined by "
                    f"{owner.__name__}); give that attribute another name"
                )

    def __set_name__(self, owner: type, name: str) -> None:
        self.name = name

    @overload
    def __get__(self, instance: None, owner: type) -> Self: ...
    @overload
    def __get__(self, instance: object, owner: type | None = None) -> NoReturn: ...
    def __get__(self, instance: Any, owner: Any = None) -> Self:
        if instance is not None:
            raise AttributeError(
                f"{self.name} isn't accessible via {type(instance).__name__} instances"
            )
        if owner is None:
            raise AttributeError(f"{self.name} needs the model class")
        if owner._meta.abstract:
            raise AttributeError(f"{self.name} isn't available; {owner.__name__} is abstract")
        return self.__bind(owner, dm.default_manager(owner))

    @classmethod
    def attach(cls, model: type, name: str = "aobjects") -> None:
        """Add the descriptor to a model that cannot declare it (``auth.User``, ``ContentType``)."""
        descriptor = cls()
        descriptor.name = name
        setattr(model, name, descriptor)

    def __bind(self, model: Any, manager: Any) -> Self:
        # Not copy.copy: binding must not run a `__copy__` a subclass defines.
        bound = object.__new__(type(self))
        bound.__dict__.update(self.__dict__)
        for slot in _slots(type(self)):
            try:
                value = object.__getattribute__(self, slot)
            except AttributeError:
                continue
            object.__setattr__(bound, slot, value)
        bound.__model = model
        bound.__manager = manager
        return bound

    def __aq_manager(self, what: str = "aq()") -> Any:
        """The bound manager, for ``aq(Model.aobjects)`` (ADR-0009 Decision 7) and the evaluators."""
        if self.__manager is None:
            raise self.__unbound(what)
        return self.__manager

    def __unbound(self, what: str) -> AttributeError:
        return AttributeError(
            f"{self.name} is not bound to a model; read {what} from the model class "
            f"(Model.{self.name})"
        )

    @property
    def model(self) -> Any:
        """The bound model: the class ``aobjects`` was read from."""
        if self.__model is None:
            raise self.__unbound("model")
        return self.__model

    # -- evaluators (step 3) ---------------------------------------------------------------------
    async def alist(self) -> list[Any]:
        """``await Model.aobjects.alist()``: the rows of a fresh QuerySet (never cached here)."""
        return await aq(self.__aq_manager("alist()")).alist()

    async def aget(self, *args: Any, **kwargs: Any) -> Any:
        """The one object matching the arguments, as ``Model.objects.get()``."""
        return await aq(self.__aq_manager("aget()")).aget(*args, **kwargs)

    async def afirst(self) -> Any:
        """The first object (ordered by ``pk`` when the QuerySet has no ordering), or ``None``."""
        return await aq(self.__aq_manager("afirst()")).afirst()

    async def acount(self) -> int:
        """The number of rows (``COUNT(*)``)."""
        return await aq(self.__aq_manager("acount()")).acount()

    async def aexists(self) -> bool:
        """Whether there is any row (a ``LIMIT 1`` query)."""
        return await aq(self.__aq_manager("aexists()")).aexists()

    def __repr__(self) -> str:
        # Never touches the database (a Manager's repr does not either).
        if self.__model is None:
            return f"<{type(self).__name__} {self.name!r} (unbound)>"
        return f"<{type(self).__name__} of {self.__model._meta.label}>"

    # -- steps 4 and 5 ---------------------------------------------------------------------------
    def __getattr__(self, name: str) -> Any:
        if name in _NOT_PROXIED or (name.startswith("__") and name.endswith("__")):
            # Protocol lookups (copy, pickle, async for) and the binding slots never reach the
            # manager; nor does contribute_to_class (ADR-0009 Decision 1).
            raise AttributeError(name)
        manager = self.__manager
        if manager is None:
            raise self.__unbound(name)
        if _is_class_data_descriptor(type(self), name):
            # A property or slot of the class that raised: never the manager's. A plain method is
            # only reached here through `super().__getattr__(name)`, which asks for the manager's.
            raise AttributeError(
                f"{type(self).__name__}.{name} raised AttributeError; it is defined on the class, "
                f"so it is not looked up on the manager"
            )
        refuse_blocked(name)
        if dm.is_queryset_copy(manager, name):
            # `manager_method` is `getattr(self.get_queryset(), name)(...)` at call time: the
            # QuerySet comes from `aq(manager)` on each call, one clone fewer than the manager.
            def copied(*args: Any, **kwargs: Any) -> Any:
                return getattr(aq(manager), name)(*args, **kwargs)

            copied.__name__ = name
            return copied
        attribute = getattr(manager, name)
        if inspect.iscoroutinefunction(attribute) or inspect.isasyncgenfunction(attribute):
            raise _refused(manager, name, "is a coroutine or an async generator")
        if not callable(attribute) or isinstance(attribute, type):
            if _is_async_value(attribute):
                # The manager owns it: refused, never closed or cancelled here.
                raise _refused(manager, name, "is an awaitable or an async generator")
            return self.__adapt(attribute, manager, django_source=False)
        django_source = (
            name not in vars(manager)
            and getattr(attribute, "__func__", None) in dm.DJANGO_QUERYSET_SOURCES
        )

        def call(*args: Any, **kwargs: Any) -> Any:
            value = attribute(*args, **kwargs)
            if _is_async_value(value):
                _discard(value)
                raise _refused(manager, name, "returned an awaitable or an async generator")
            return self.__adapt(value, manager, django_source)

        call.__name__ = name
        call.__doc__ = getattr(attribute, "__doc__", None)
        return call

    def __adapt(self, value: Any, manager: Any, django_source: bool) -> Any:
        """Re-wrap what the manager handed out (ADR-0009 Decision 4): nothing sync leaks."""
        if isinstance(value, dm.BaseManager):
            if value.model is self.__model:
                return self.__bind(value.model, value)
            # Another model's manager: this subclass's methods were written for our model.
            return AsyncObjects().__bind(value.model, value)
        if isinstance(value, QuerySet):
            if django_source:
                return manager_queryset(value)
            return wrap_result(value)
        return value


def _slots(cls: type) -> list[str]:
    names: list[str] = []
    for klass in cls.__mro__:
        declared = vars(klass).get("__slots__", ())
        names.extend([declared] if isinstance(declared, str) else declared)
    return [slot for slot in names if slot not in ("__dict__", "__weakref__")]


def _is_class_data_descriptor(cls: type, name: str) -> bool:
    for klass in cls.__mro__:
        if name in vars(klass):
            return inspect.isdatadescriptor(vars(klass)[name])
    return False


def _is_async_value(value: Any) -> bool:
    return inspect.isawaitable(value) or inspect.isasyncgen(value)


def _discard(value: Any) -> None:
    """Make sure a refused awaitable a call just returned never runs: a coroutine has not started
    (so no thread has) and is closed; a scheduled future or task is cancelled; an unstarted async
    generator needs nothing."""
    if inspect.iscoroutine(value):
        value.close()
    elif isinstance(value, asyncio.Future):
        value.cancel()


def _refused(manager: Any, name: str, why: str) -> NotImplementedError:
    return NotImplementedError(
        f"{type(manager).__name__}.{name} {why}: not supported on aobjects, because Django's "
        f"async manager methods run on sync_to_async (a thread); write an async method on an "
        f"AsyncObjects subclass that awaits the evaluators instead"
    )
