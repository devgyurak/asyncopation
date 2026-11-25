"""The decoder shim against asyncmy's own table and mysqlclient's (what Django hands it)."""

from __future__ import annotations

from typing import Any

from tests.support.mysql_driver_drift import my_config
from tests.support.mysql_reference import normalise


def shim() -> dict[Any, Any]:
    """The `conv` table the driver passes to asyncmy."""
    return dict(my_config().conn_kwargs["conv"])


def asyncmy_defaults() -> dict[Any, Any]:
    import asyncmy.converters

    return dict(asyncmy.converters.conversions)


def keys_that_differ_from_asyncmy() -> set[Any]:
    """Keys whose decoder is not asyncmy's own."""
    ours, default = shim(), asyncmy_defaults()
    return {key for key in ours if ours[key] is not default.get(key)}


def keys_missing_from_the_shim() -> set[Any]:
    return set(asyncmy_defaults()) - set(shim())


def extra_keys_in_the_shim() -> set[Any]:
    return set(shim()) - set(asyncmy_defaults())


def _field_type(name: str) -> int:
    from asyncmy.constants import FIELD_TYPE

    return getattr(FIELD_TYPE, name)


def decoded_by_the_shim(name: str, raw: str) -> tuple[str, str]:
    """(type name, repr) of what the shim's decoder for FIELD_TYPE.`name` returns for `raw`."""
    return normalise([[shim()[_field_type(name)](raw)]])[0][0]


def decoded_by_mysqlclient(name: str, raw: str) -> tuple[str, str]:
    """The same through Django's mysqlclient conversions."""
    from django.db.backends.mysql.base import django_conversions

    return normalise([[django_conversions[_field_type(name)](raw)]])[0][0]


def differing_type_names() -> set[str]:
    """`FIELD_TYPE` names whose decoder is not asyncmy's own."""
    from asyncmy.constants import FIELD_TYPE

    names = {value: name for name, value in vars(FIELD_TYPE).items() if not name.startswith("_")}
    return {names[key] for key in keys_that_differ_from_asyncmy() if isinstance(key, int)}


def added_encoder_names() -> set[str]:
    """Names of the encoder keys (Python classes) the shim adds to asyncmy's table."""
    return {key.__name__ for key in extra_keys_in_the_shim() if isinstance(key, type)}


def added_non_encoder_keys() -> set[Any]:
    """Keys the shim adds that are not Python classes (there must be none)."""
    return {key for key in extra_keys_in_the_shim() if not isinstance(key, type)}


def time_decoder_is_djangos() -> bool:
    from django.db.backends.utils import typecast_time

    return shim()[_field_type("TIME")] is typecast_time
