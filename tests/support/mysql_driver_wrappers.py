"""Small helpers of the MySQL driver and engine unit tests (the compile-wrapper ones are in
`mysql_wrappers.py`, from the compile-only wrapper work)."""

from __future__ import annotations

import itertools
from typing import Any

MY_SETTINGS: dict[str, Any] = {
    "ENGINE": "django.db.backends.mysql",
    "NAME": "x",
    "OPTIONS": {},
    "TIME_ZONE": None,
    "CONN_MAX_AGE": 0,
    "CONN_HEALTH_CHECKS": False,
    "AUTOCOMMIT": True,
    "ATOMIC_REQUESTS": False,
    "USER": "",
    "PASSWORD": "",
    "HOST": "",
    "PORT": "",
    "TEST": {},
}


_COUNTER = itertools.count()


def unique_my_alias() -> str:
    return f"asyncopation_my_unit_{next(_COUNTER)}"


def forget_my_wrapper(alias: str) -> None:
    from asyncopation.compile import mysql

    mysql._WRAPPERS.pop(alias, None)


def registered_wrapper(alias: str) -> Any:
    """The wrapper `compile.mysql` keeps for `alias` (None when there is none)."""
    from asyncopation.compile import mysql

    return mysql._WRAPPERS.get(alias)


def field_names(cls: Any) -> set[str]:
    """Names of the dataclass fields of `cls`."""
    import dataclasses

    return {field.name for field in dataclasses.fields(cls)}
