"""Helpers for the Django/asgiref drift tests of the connection boundary (ADR-0008)."""

from __future__ import annotations

import inspect
from typing import Any

from django.utils.functional import cached_property

from tests.support.compile_wrappers import make_real_pg_wrapper
from tests.support.connect_counter import count_sync_connection_attempts


def static_is_cached_property(owner: type, name: str) -> bool:
    return isinstance(inspect.getattr_static(owner, name), cached_property)


def injected_version_and_attempts(alias: str, version: int) -> tuple[Any, int]:
    wrapper = make_real_pg_wrapper(alias)
    wrapper.__dict__["pg_version"] = version
    with count_sync_connection_attempts() as counter:
        served = wrapper.pg_version
    return served, counter.count


def source_contains(obj: Any, fragment: str) -> bool:
    return fragment in inspect.getsource(obj)
