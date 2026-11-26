"""What the manager-level ``aobjects`` object reads from Django's managers (ADR-0009 Decisions 4, 6).

``BaseManager._get_queryset_methods`` copies QuerySet methods onto a manager class as
``manager_method(self, *a, **kw): return getattr(self.get_queryset(), name)(*a, **kw)``. Every
copy shares that one code object, which nothing else (``functools.wraps``, a user decorator, a
new ``from_queryset`` class) changes, so it is how a copy is told from a method the manager class
defines itself. The code object is read once from ``Manager.filter``; if Django ever changes the
copies, ``MANAGER_METHOD_CODE`` is ``None`` and every name is taken from the manager, which is
always correct (one QuerySet clone more). Nothing here runs I/O.
"""

from __future__ import annotations

import weakref
from types import CodeType, FunctionType
from typing import Any

from django.db.models import Manager
from django.db.models.manager import BaseManager

__all__ = [
    "DJANGO_QUERYSET_SOURCES",
    "MANAGER_METHOD_CODE",
    "BaseManager",
    "default_manager",
    "is_queryset_copy",
]

# `co_qualname` of the copies `_get_queryset_methods` makes (a drift test pins it).
_COPY_QUALNAME = f"{BaseManager._get_queryset_methods.__qualname__}.<locals>.create_method.<locals>.manager_method"


def _copy_code() -> CodeType | None:
    code = getattr(Manager.filter, "__code__", None)
    if isinstance(code, CodeType) and code.co_qualname == _COPY_QUALNAME:
        return code
    return None


MANAGER_METHOD_CODE: CodeType | None = _copy_code()

# Django's own manager methods that return a fresh ``get_queryset()`` result (``all()`` returns it
# unchained), whatever ``get_queryset`` override they call.
DJANGO_QUERYSET_SOURCES = frozenset({BaseManager.all, BaseManager.get_queryset})

# Per manager class: name -> copied by `_get_queryset_methods`. Weak keys, because `from_queryset`
# creates classes at run time; a class patched after its first lookup is not re-read.
_ROUTES: weakref.WeakKeyDictionary[type, dict[str, bool]] = weakref.WeakKeyDictionary()


def _resolve_copy(cls: type, name: str) -> bool:
    for klass in cls.__mro__:
        if name in vars(klass):
            value = vars(klass)[name]
            return (
                isinstance(value, FunctionType)
                and value.__code__ is MANAGER_METHOD_CODE
                and value.__name__ == name
            )
    return False


def is_queryset_copy(manager: Any, name: str) -> bool:
    """Whether ``name`` on ``manager`` is a QuerySet method Django copied, unchanged, onto its class.

    An attribute in the manager instance's ``__dict__``, a method the class (or a base) defines,
    an override, an alias (``published = Manager.filter``: same code, other name) and a decorated
    copy are not.
    """
    if MANAGER_METHOD_CODE is None or name in vars(manager):
        return False
    cls = type(manager)
    routes = _ROUTES.get(cls)
    if routes is None:
        routes = _ROUTES[cls] = {}
    copied = routes.get(name)
    if copied is None:
        copied = routes[name] = _resolve_copy(cls, name)
    return copied


def default_manager(model: Any) -> Any:
    """``model._default_manager``: the per-class manager copy whose ``model`` is ``model``."""
    return model._default_manager
