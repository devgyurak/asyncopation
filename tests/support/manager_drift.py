"""Helpers for the drift tests of Django's manager method copies (`BaseManager._get_queryset_methods`,
ADR-0009 Decision 6): read from Django itself, plus the one constant Asyncopation takes from it."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from django.db.models import Manager

MANAGER_METHOD_QUALNAME = (
    "BaseManager._get_queryset_methods.<locals>.create_method.<locals>.manager_method"
)


@dataclass
class RecordingQuerySet:
    """Stands in for the QuerySet `get_queryset()` returns: records each method call on it."""

    calls: list[tuple[str, tuple[Any, ...], dict[str, Any]]] = field(default_factory=list)

    def __getattr__(self, name: str) -> Any:
        def method(*args: Any, **kwargs: Any) -> str:
            self.calls.append((name, args, kwargs))
            return f"result of {name}"

        return method


class StubManager(Manager):
    """A Manager whose `get_queryset()` returns one `RecordingQuerySet`."""

    def __init__(self) -> None:
        super().__init__()
        self.recorder = RecordingQuerySet()

    def get_queryset(self) -> Any:
        return self.recorder


@dataclass(frozen=True)
class Dispatch:
    result: Any
    calls: list[tuple[str, tuple[Any, ...], dict[str, Any]]]


def dispatch_of_copy(name: str, *args: Any, **kwargs: Any) -> Dispatch:
    """Call Django's copy `Manager.<name>` on a `StubManager`: what it returned and the calls it made
    on the object `get_queryset()` returned."""
    manager = StubManager()
    result = getattr(Manager, name)(manager, *args, **kwargs)
    return Dispatch(result, list(manager.recorder.calls))


def generated_base() -> type:
    """The class `BaseManager.from_queryset(QuerySet)` generated, which `Manager` subclasses."""
    return Manager.__mro__[1]


def code_of(cls: type, name: str) -> Any:
    return vars(cls)[name].__code__


def helper_manager_method_code() -> Any:
    """`asyncopation._django.managers.MANAGER_METHOD_CODE` (imported here, so a missing module fails
    one test, not the collection of the whole drift module)."""
    from asyncopation._django.managers import MANAGER_METHOD_CODE

    return MANAGER_METHOD_CODE
