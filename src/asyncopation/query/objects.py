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

import inspect
from typing import Any, NoReturn, Self, overload

from django.db.models import Manager, QuerySet

from asyncopation._django import managers as dm
from asyncopation.query.queryset import aq, manager_queryset, refuse_blocked, wrap_result

__all__ = ["AsyncObjects"]

# Names a subclass may not shadow (ADR-0009 Decision 3, step 1).
_PROTECTED = ("model", "name")

# The manager-level evaluators (step 3): the single source of their names.
EVALUATORS = ("alist", "aget", "afirst", "acount", "aexists")

# Never proxied to the manager (ADR-0009 Decision 1): the binding slots, and
# `contribute_to_class`, which Django calls on any class-body or `add_to_class` value that has it.
_NOT_PROXIED = frozenset({"_model", "_manager", "contribute_to_class"})


class AsyncObjects:
    """Declare ``aobjects = AsyncObjects()`` on a model, or ``AsyncObjects.attach(Model)``.

    Subclass it to add async manager methods; they do I/O through the evaluators reached from
    ``self`` (``await self.filter(...).aget(...)``) and read the bound model as ``self.model``.
    A subclass must be constructible without arguments (``attach`` calls ``cls()``); its
    ``__init__`` runs once, and every bound object copies the declared object's ``__dict__``.

    Unlike Django's manager descriptor it does not refuse a swapped-out model (``AUTH_USER_MODEL``
    replacing ``auth.User``): guard A102 reserves the ``swapped`` name for ``compile/scope.py``, so
    attach it to the model that is in use.
    """

    name: str = "aobjects"
    # The binding: None on the declared (unbound) object.
    _model: Any = None
    _manager: Any = None

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
        return self._bind(owner, dm.default_manager(owner))

    @classmethod
    def attach(cls, model: type, name: str = "aobjects") -> None:
        """Add the descriptor to a model that cannot declare it (``auth.User``, ``ContentType``)."""
        descriptor = cls()
        descriptor.name = name
        setattr(model, name, descriptor)

    def _bind(self, model: Any, manager: Any) -> Self:
        # Not copy.copy: binding must not run a `__copy__` a subclass defines.
        bound = object.__new__(type(self))
        bound.__dict__.update(self.__dict__)
        bound._model = model
        bound._manager = manager
        return bound

    def _aq_manager(self) -> Any:
        """The bound manager, for ``aq(Model.aobjects)`` (ADR-0009 Decision 7)."""
        if self._manager is None:
            raise self._unbound("aq()")
        return self._manager

    def _unbound(self, what: str) -> AttributeError:
        return AttributeError(
            f"{self.name} is not bound to a model; read {what} from the model class "
            f"(Model.{self.name})"
        )

    @property
    def model(self) -> Any:
        """The bound model: the class ``aobjects`` was read from."""
        if self._model is None:
            raise self._unbound("model")
        return self._model

    # -- evaluators (step 3) ---------------------------------------------------------------------
    async def alist(self) -> list[Any]:
        """``await Model.aobjects.alist()``: the rows of a fresh QuerySet (never cached here)."""
        return await aq(self._aq_manager()).alist()

    async def aget(self, *args: Any, **kwargs: Any) -> Any:
        """The one object matching the arguments, as ``Model.objects.get()``."""
        return await aq(self._aq_manager()).aget(*args, **kwargs)

    async def afirst(self) -> Any:
        """The first object (ordered by ``pk`` when the QuerySet has no ordering), or ``None``."""
        return await aq(self._aq_manager()).afirst()

    async def acount(self) -> int:
        """The number of rows (``COUNT(*)``)."""
        return await aq(self._aq_manager()).acount()

    async def aexists(self) -> bool:
        """Whether there is any row (a ``LIMIT 1`` query)."""
        return await aq(self._aq_manager()).aexists()

    def __repr__(self) -> str:
        # Never touches the database (a Manager's repr does not either).
        if self._model is None:
            return f"<{type(self).__name__} {self.name!r} (unbound)>"
        return f"<{type(self).__name__} of {self._model._meta.label}>"

    # -- steps 4 and 5 ---------------------------------------------------------------------------
    def __getattr__(self, name: str) -> Any:
        if name in _NOT_PROXIED or (name.startswith("__") and name.endswith("__")):
            # Protocol lookups (copy, pickle, async for) and the binding slots never reach the
            # manager; nor does contribute_to_class (ADR-0009 Decision 1).
            raise AttributeError(name)
        manager = self._manager
        if manager is None:
            raise self._unbound(name)
        refuse_blocked(name)
        if dm.is_queryset_copy(manager, name):
            # `manager_method` is exactly `getattr(self.get_queryset(), name)`: one clone fewer.
            return getattr(aq(manager), name)
        attribute = getattr(manager, name)
        if inspect.iscoroutinefunction(attribute) or inspect.isasyncgenfunction(attribute):
            raise _refused(manager, name, "is a coroutine or an async generator")
        if not callable(attribute) or isinstance(attribute, type):
            return self._adapt(attribute, manager, name, django_source=False)
        django_source = getattr(type(manager), name, None) in dm.DJANGO_QUERYSET_SOURCES

        def call(*args: Any, **kwargs: Any) -> Any:
            return self._adapt(attribute(*args, **kwargs), manager, name, django_source)

        call.__name__ = name
        call.__doc__ = getattr(attribute, "__doc__", None)
        return call

    def _adapt(self, value: Any, manager: Any, name: str, django_source: bool) -> Any:
        """Re-wrap what the manager handed out (ADR-0009 Decision 4): nothing sync leaks."""
        if inspect.isawaitable(value) or inspect.isasyncgen(value):
            # It has not started, so no thread has: close a coroutine so it is not left pending.
            close = getattr(value, "close", None)
            if inspect.iscoroutine(value) and close is not None:
                close()
            raise _refused(manager, name, "returned an awaitable or an async generator")
        if isinstance(value, Manager):
            return self._bind(value.model, value)
        if isinstance(value, QuerySet):
            if django_source:
                return manager_queryset(value)
            return wrap_result(value)
        return value


def _refused(manager: Any, name: str, why: str) -> NotImplementedError:
    return NotImplementedError(
        f"{type(manager).__name__}.{name} {why}: not supported on aobjects, because Django's "
        f"async manager methods run on sync_to_async (a thread); write an async method on an "
        f"AsyncObjects subclass that awaits the evaluators instead"
    )
