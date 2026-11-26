"""Drift helpers: the ported MySQL connection logic against upstream Django (ADR-0003, ADR-0007).

Upstream Django's MySQL wrapper (mysqlclient) is imported directly here; the driver side uses a
literal settings dict so no database configuration is involved.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass
from typing import Any

MY_DRIFT_SETTINGS: dict[str, Any] = {
    "ENGINE": "django.db.backends.mysql",
    "NAME": "db",
    "USER": "user",
    "PASSWORD": "secret",
    "HOST": "db.example",
    "PORT": 3306,
    "OPTIONS": {},
    "TIME_ZONE": None,
    "CONN_MAX_AGE": 0,
    "CONN_HEALTH_CHECKS": False,
    "AUTOCOMMIT": True,
    "ATOMIC_REQUESTS": False,
    "DISABLE_SERVER_SIDE_CURSORS": False,
    "TEST": {},
}

# Differences from Django's `get_connection_params` that ADR-0003 documents.
DOCUMENTED_DIFFERENCES = frozenset({"autocommit", "init_command", "conv", "sql_mode"})


def my_drift_settings(options: dict[str, Any] | None = None, **extra: Any) -> dict[str, Any]:
    settings_dict = copy.deepcopy(MY_DRIFT_SETTINGS)
    settings_dict["OPTIONS"] = dict(options or {})
    settings_dict.update(extra)
    return settings_dict


def real_my_wrapper(settings_dict: dict[str, Any]) -> Any:
    from django.db.backends.mysql.base import DatabaseWrapper

    return DatabaseWrapper(copy.deepcopy(settings_dict), "default")


def build_my_config(settings_dict: dict[str, Any]) -> Any:
    from asyncopation.drivers.mysql.settings import build_config

    return build_config("default", settings_dict, use_tz=True, time_zone="UTC")


def my_config(options: dict[str, Any] | None = None, **extra: Any) -> Any:
    return build_my_config(my_drift_settings(options, **extra))


def my_config_error(options: dict[str, Any] | None = None, **extra: Any) -> BaseException | None:
    """The exception `build_config` raises for these settings (None when it accepts them)."""
    try:
        my_config(options, **extra)
    except Exception as exc:
        return exc
    return None


def django_config_error(
    options: dict[str, Any] | None = None, **extra: Any
) -> BaseException | None:
    """The exception Django's `get_connection_params` raises for the same settings."""
    try:
        real_my_wrapper(my_drift_settings(options, **extra)).get_connection_params()
    except Exception as exc:
        return exc
    return None


@dataclass(frozen=True)
class ConnKwargsComparison:
    ours: dict[str, Any]
    reference: dict[str, Any]
    reference_isolation: str | None
    our_isolation: str | None

    def differences(self) -> dict[str, tuple[Any, Any]]:
        """Keys whose values differ, minus the documented differences."""
        keys = (set(self.ours) | set(self.reference)) - DOCUMENTED_DIFFERENCES
        return {
            k: (self.ours.get(k), self.reference.get(k))
            for k in sorted(keys)
            if self.ours.get(k) != self.reference.get(k)
        }


def compare_conn_kwargs(
    options: dict[str, Any] | None = None, **extra: Any
) -> ConnKwargsComparison:
    settings_dict = my_drift_settings(options, **extra)
    config = build_my_config(settings_dict)
    wrapper = real_my_wrapper(settings_dict)
    reference = wrapper.get_connection_params()
    return ConnKwargsComparison(
        dict(config.conn_kwargs), reference, wrapper.isolation_level, config.isolation_level
    )


def django_conversions_table() -> dict[Any, Any]:
    from django.db.backends.mysql.base import django_conversions

    return dict(django_conversions)


def pool_values(pool: Any = None, **extra: Any) -> dict[str, Any]:
    """The pool settings of a config built with `ASYNCOPATION["pool"] = pool`, under the names
    users write (`min_size`, `max_size`, `timeout`, `max_waiting`, `max_idle`).

    The config stores them the way asyncmy and the driver consume them: `pool_kwargs` holds
    `minsize` / `maxsize` / `pool_recycle`; `acquire_timeout` and `max_waiting` are attributes.
    """
    asyncopation = {} if pool is None else {"ASYNCOPATION": {"pool": pool}}
    config = my_config(None, **asyncopation, **extra)
    return {
        "min_size": config.pool_kwargs["minsize"],
        "max_size": config.pool_kwargs["maxsize"],
        "max_idle": config.pool_kwargs["pool_recycle"],
        "timeout": config.acquire_timeout,
        "max_waiting": config.max_waiting,
    }


def pool_error(pool: Any) -> BaseException | None:
    try:
        pool_values(pool)
    except Exception as exc:
        return exc
    return None


def offline_my_driver(options: dict[str, Any] | None = None, **extra: Any) -> Any:
    """A `MySQLDriver` built from the literal settings (no I/O, never opened)."""
    from asyncopation.drivers.mysql import MySQLDriver

    return MySQLDriver.from_settings(
        "default", my_drift_settings(options, **extra), use_tz=True, time_zone="UTC"
    )


def driver_from_config(config: Any) -> Any:
    from asyncopation.drivers.mysql import MySQLDriver

    return MySQLDriver(config)


def construct_with_asyncio_and_sockets_forbidden() -> Any:
    """Build a driver while every asyncio primitive constructor and `socket.connect` raise."""
    import asyncio
    import contextlib
    import socket
    from unittest import mock

    names = ("Lock", "Event", "Semaphore", "Condition", "Queue", "Future", "Task")
    with contextlib.ExitStack() as stack:
        for name in names:
            stack.enter_context(mock.patch.object(asyncio, name, side_effect=AssertionError(name)))
        stack.enter_context(mock.patch.object(socket.socket, "connect", side_effect=AssertionError))
        stack.enter_context(
            mock.patch.object(asyncio, "get_running_loop", side_effect=AssertionError)
        )
        return offline_my_driver()


def sync_error(call: Any) -> BaseException | None:
    """The exception `call()` raises (None when it returns)."""
    try:
        call()
    except Exception as exc:
        return exc
    return None
