"""``AsyncObjects``: the ``aobjects`` descriptor (ADR-0006 Decision 3).

A plain descriptor, not a Django ``Manager``: it defines no ``contribute_to_class``, so it never
lands in ``_meta.managers``, never becomes ``_default_manager`` and never suppresses the implicit
``objects``. ``Model.aobjects`` is ``aq(owner._default_manager.get_queryset())`` for the class it is
accessed on (abstract bases and multi-table inheritance included).
"""

from __future__ import annotations

from typing import Any

from asyncopation.query.queryset import AsyncQuerySet, aq

__all__ = ["AsyncObjects"]


class AsyncObjects:
    """Declare ``aobjects = AsyncObjects()`` on a model, or ``AsyncObjects.attach(Model)``.

    Unlike Django's manager descriptor it does not refuse a swapped-out model (``AUTH_USER_MODEL``
    replacing ``auth.User``): guard A102 reserves the ``swapped`` name for ``compile/scope.py``, so
    attach it to the model that is in use.
    """

    name: str = "aobjects"

    def __set_name__(self, owner: type, name: str) -> None:
        self.name = name

    def __get__(self, instance: Any, owner: Any = None) -> AsyncQuerySet:
        if instance is not None:
            raise AttributeError(
                f"{self.name} isn't accessible via {type(instance).__name__} instances"
            )
        if owner is None:
            raise AttributeError(f"{self.name} needs the model class")
        if owner._meta.abstract:
            raise AttributeError(f"{self.name} isn't available; {owner.__name__} is abstract")
        return aq(owner._default_manager.get_queryset())

    @classmethod
    def attach(cls, model: type, name: str = "aobjects") -> None:
        """Add the descriptor to a model that cannot declare it (``auth.User``, ``ContentType``)."""
        descriptor = cls()
        descriptor.name = name
        setattr(model, name, descriptor)
