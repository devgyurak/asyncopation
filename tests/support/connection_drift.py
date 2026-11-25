"""Helpers for the Django/asgiref drift tests of the connection boundary (ADR-0008)."""

from __future__ import annotations

import functools
import inspect
import warnings
from typing import Any

from django.utils import functional

from tests.support.compile_wrappers import make_real_pg_wrapper
from tests.support.connect_counter import count_sync_connection_attempts


def legacy_cached_property() -> type | None:
    """``django.utils.functional.cached_property`` while Django still ships it.

    It is deprecated on Django main (ticket #30949) and will be removed. Looked up with warnings
    silenced so a deprecation raised on access cannot fail the suite (``filterwarnings = error``).
    """
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        return getattr(functional, "cached_property", None)


def cached_property_classes() -> tuple[type, ...]:
    """Decorator classes Django has used for cached properties (the legacy one only if present)."""
    legacy = legacy_cached_property()
    return (functools.cached_property, *((legacy,) if legacy is not None else ()))


def static_is_cached_property(owner: type, name: str) -> bool:
    return isinstance(inspect.getattr_static(owner, name), cached_property_classes())


def injected_version_and_attempts(alias: str, version: int) -> tuple[Any, int]:
    wrapper = make_real_pg_wrapper(alias)
    wrapper.__dict__["pg_version"] = version
    with count_sync_connection_attempts() as counter:
        served = wrapper.pg_version
    return served, counter.count


def source_contains(obj: Any, fragment: str) -> bool:
    return fragment in inspect.getsource(obj)
