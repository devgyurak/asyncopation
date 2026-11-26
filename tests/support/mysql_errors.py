"""asyncmy exceptions and what the driver's translation makes of them."""

from __future__ import annotations

from typing import Any

CRYPTOGRAPHY_MESSAGE = (
    "'cryptography' package is required for sha256_password or caching_sha2_password auth methods"
)


def asyncmy_error(name: str, *args: Any) -> Exception:
    """An instance of `asyncmy.errors.<name>`."""
    import asyncmy.errors

    return getattr(asyncmy.errors, name)(*args)


def translated(exc: BaseException) -> BaseException:
    """What `translate_errors()` raises for `exc` raised in its block."""
    from asyncopation.drivers.mysql.errors import translate_errors

    try:
        with translate_errors():
            raise exc
    except BaseException as raised:
        return raised
    raise AssertionError("translate_errors must not swallow")


def translated_class(exc: BaseException) -> type[BaseException]:
    return type(translated(exc))


def mapped(name: str, *args: Any) -> BaseException:
    """The Django exception the driver raises for `asyncmy.errors.<name>(*args)` (with cause)."""
    return translated(asyncmy_error(name, *args))


def is_disconnect(exc: BaseException) -> bool:
    from asyncopation.drivers.mysql.errors import is_disconnect as classify

    return classify(exc)
