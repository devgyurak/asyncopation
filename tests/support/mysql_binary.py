"""Binary parameter encoding: unit helpers and the values of the integration round trips."""

from __future__ import annotations

from collections import namedtuple
from decimal import Decimal
from enum import IntEnum, StrEnum
from typing import Any


class TaggedBytes(bytes):
    """A `bytes` subclass (what an ORM field type might hand over)."""


TRICKY = b"a\\b'c\x00d"
INJECTION = b"x'; SELECT 99 -- "

NO_BACKSLASH_ESCAPES = {"init_command": "SET sql_mode='NO_BACKSLASH_ESCAPES'"}


class FakeCursor:
    def mogrify(self, sql: str, params: Any) -> str:
        return sql % (params if isinstance(params, dict) else tuple(params))


class FakeRaw:
    """The two things `_prepare_params` reads from a connection."""

    _encoding = "utf8"

    def __init__(self, *, no_backslash_escapes: bool) -> None:
        self.server_status = (
            512 if no_backslash_escapes else 0
        )  # SERVER_STATUS_NO_BACKSLASH_ESCAPES

    def cursor(self, *_args: Any) -> FakeCursor:
        return FakeCursor()

    def literal(self, value: Any) -> str:
        return f"<{value}>"

    def escape_string(self, value: str) -> str:
        return f"E<{value}>"


def prepare(params: Any, *, no_backslash_escapes: bool = False) -> Any:
    """`_prepare_params` for a session with or without `NO_BACKSLASH_ESCAPES`."""
    from asyncopation.drivers.mysql.connection import _prepare_params

    return _prepare_params(params, FakeRaw(no_backslash_escapes=no_backslash_escapes))


def expected_subclass_text(value: Any, no_backslash_escapes: bool) -> list[str]:
    """What `literal_texts` shows for a container subclass: its `str()`, quoted in hex mode."""
    return [f"'E<{value}>'" if no_backslash_escapes else str(value)]


def format_with(value: Any, sql: str) -> str:
    """`_format` of `sql` with one parameter, against fakes (no server)."""
    from asyncopation.drivers.mysql.connection import _format

    return _format(FakeRaw(no_backslash_escapes=False), FakeCursor(), sql, [value]).decode()


def kinds(params: Any) -> list[str]:
    """Type names of the params after preparing (positional or dict values)."""
    values = params.values() if isinstance(params, dict) else params
    return [type(v).__name__ for v in values]


def literal_texts(params: Any) -> list[Any]:
    """The text of every `SqlLiteral` in `params`; other values as they are."""
    from asyncopation.drivers.mysql.settings import SqlLiteral

    values = params.values() if isinstance(params, dict) else params
    return [v.text if isinstance(v, SqlLiteral) else v for v in values]


def encoded(text: str) -> str:
    from asyncopation.drivers.mysql.settings import SqlLiteral, encode_literal

    return encode_literal(SqlLiteral(text))


def conversion_for_sql_literal() -> Any:
    from asyncopation.drivers.mysql.settings import SqlLiteral, build_conversions

    return build_conversions()[SqlLiteral]


def encode_literal_function() -> Any:
    from asyncopation.drivers.mysql.settings import encode_literal

    return encode_literal


# --------------------------------------------------------------------------- integration side
def _table() -> str:
    from tests.support.mysql_env import RUN_ID

    return f"asyncopation_binary_{RUN_ID}"


async def create_binary_table() -> str:
    from tests.support.mysql_env import admin_query

    table = _table()
    await admin_query(f"DROP TABLE IF EXISTS {table}")
    await admin_query(
        f"CREATE TABLE {table} (id INT AUTO_INCREMENT PRIMARY KEY, b LONGBLOB NULL, v VARBINARY(64) NULL)"
    )
    return table


async def drop_binary_table() -> None:
    from tests.support.mysql_env import admin_query

    await admin_query(f"DROP TABLE IF EXISTS {_table()}")


async def selected_back(options: dict[str, Any], value: Any, *, as_dict: bool = False) -> Any:
    """`SELECT <param>` through the driver (positional, or a named dict parameter)."""
    from tests.support.mysql_env import open_my_driver

    async with open_my_driver(options=options) as driver, driver.connection() as conn:
        if as_dict:
            rows = await conn.fetch_all("SELECT %(a)s", {"a": value})
        else:
            rows = await conn.fetch_all("SELECT %s", (value,))
    return rows[0][0]


async def stored_and_read_back(options: dict[str, Any], table: str, value: Any) -> tuple[Any, Any]:
    """Insert `value` into the BLOB and VARBINARY columns through `execute`; read both back."""
    from tests.support.mysql_env import open_my_driver

    async with open_my_driver(options=options) as driver, driver.connection() as conn:
        await conn.execute(f"INSERT INTO {table} (b, v) VALUES (%s, %s)", (value, value))
        rows = await conn.fetch_all(f"SELECT b, v FROM {table}", None)
    return rows[0]


async def stored_by_execute_many(
    options: dict[str, Any], table: str, values: list[Any]
) -> list[Any]:
    """`execute_many` INSERT of one bytes parameter per set; the stored values in order."""
    from tests.support.mysql_env import open_my_driver

    async with open_my_driver(options=options) as driver, driver.connection() as conn:
        await conn.execute_many(f"INSERT INTO {table} (b) VALUES (%s)", [(v,) for v in values])
        rows = await conn.fetch_all(f"SELECT b FROM {table} ORDER BY id", None)
    return [row[0] for row in rows]


async def stored_and_read_back_by_mysqlclient(
    options: dict[str, Any], table: str, value: Any
) -> tuple[Any, Any]:
    """The row `stored_and_read_back` wrote, read through mysqlclient."""
    from tests.support.mysql_reference import sync_rows

    await stored_and_read_back(options, table, value)
    return (await sync_rows(options, f"SELECT b, v FROM {table}"))[0]


BIG_BLOB_SIZE = 9 * 1024 * 1024  # a hex literal (2x) would exceed MariaDB's 16 MiB packet limit


async def big_blob_stored_length(table: str) -> int:
    """Insert a 9 MiB blob through `execute` with the default sql_mode; the stored length."""
    from tests.support.mysql_env import open_my_driver

    async with open_my_driver() as driver, driver.connection() as conn:
        await conn.execute(f"INSERT INTO {table} (b) VALUES (%s)", (b"\x01" * BIG_BLOB_SIZE,))
        rows = await conn.fetch_all(f"SELECT LENGTH(b) FROM {table}", None)
    return rows[0][0]


class Weird:
    """A type neither asyncmy nor mysqlclient has an encoder for; its text breaks out of a literal."""

    def __str__(self) -> str:
        return WEIRD_TEXT


WEIRD_TEXT = "x' OR '1'='1\\"


class FakeEscapingRaw:
    """`literal` and `escape_string` of a connection, made visible: `L(..)` and `E<..>`."""

    def literal(self, value: Any) -> str:
        return f"L({value!r})"

    def escape_string(self, value: str) -> str:
        return f"E<{value}>"


def rendered(value: Any) -> str:
    from asyncopation.drivers.mysql.connection import _render

    return _render(FakeEscapingRaw(), value)


# --------------------------------------------------------------------------- exact types
QUOTED_TEXT = "1' OR '1'='1"


class QuotedInt(int):
    def __str__(self) -> str:
        return QUOTED_TEXT


class QuotedFloat(float):
    def __str__(self) -> str:
        return QUOTED_TEXT


class QuotedDecimal(Decimal):
    def __str__(self) -> str:
        return QUOTED_TEXT


Point = namedtuple("Point", "x y")


class TupleSubclass(tuple):
    pass


class FrozenSubclass(frozenset):
    pass


class NonFiniteFloat(float):
    pass


class TextChoice(StrEnum):
    """A `str` mixin member whose `str()` is the member name, not the value (as Django's
    `TextChoices` is not, but a user's enum may be)."""

    A = "va'l"

    def __str__(self) -> str:
        return self.name


class QuotedIntEnum(IntEnum):
    FIVE = 5

    def __str__(self) -> str:
        return QUOTED_TEXT


def quoted_integer_choice() -> Any:
    """A member of a Django `IntegerChoices` whose `__str__` carries a quote."""
    from django.db import models

    class Level(models.IntegerChoices):
        FIVE = 5, "five"

        def __str__(self) -> str:
            return QUOTED_TEXT

    return Level.FIVE


def exact_type_cases() -> list[Any]:
    """(sql, params) cases: number subclass values at the top level and nested.

    mysqlclient sends such a value as its quoted `str()`; the tests compare the driver's outcome
    with the sync backend's, never with a fixed expectation.
    """
    import pytest

    choice = quoted_integer_choice()
    values = {
        "int-subclass": (QuotedInt(5), 5),
        "float-subclass": (QuotedFloat(1.5), 1.5),
        "decimal-subclass": (QuotedDecimal("1.5"), Decimal("1.5")),
        "int-enum": (QuotedIntEnum.FIVE, 5),
        "integer-choices": (choice, 5),
    }
    cases = []
    for name, (value, plain) in values.items():
        cases.append(pytest.param("SELECT %s", [value], id=f"{name}-top"))
        cases.append(pytest.param("SELECT %s = %s", [value, "5"], id=f"{name}-equals-its-text"))
        cases.append(pytest.param("SELECT %s IN %s", [plain, (value,)], id=f"{name}-in-tuple"))
        cases.append(pytest.param("SELECT %s IN %s", [plain, [value]], id=f"{name}-in-list"))
    cases.append(
        pytest.param(
            "SELECT ('1' = %s) + ('1.0' = %s) + ('x' = %s)",
            [choice, choice, choice],
            id="varchar-compares-as-text",
        )
    )
    return cases


def nested_text_choice_params() -> tuple[Any, ...]:
    return ((TextChoice.A,), [TextChoice.A])


def number_subclass_params() -> tuple[Any, ...]:
    return (QuotedInt(5), QuotedFloat(1.5), QuotedDecimal("1.5"), QuotedIntEnum.FIVE)


def prepared_number_kinds(*, no_backslash_escapes: bool) -> list[str]:
    """Type names `_prepare_params` leaves for the number subclasses (top-level values)."""
    return kinds(prepare(number_subclass_params(), no_backslash_escapes=no_backslash_escapes))


def rendered_numbers() -> list[str]:
    """`_render` of each number subclass (the connection's `literal` shows as `L(..)`)."""
    return [rendered(v) for v in number_subclass_params()]


def wrap_top(value: Any) -> tuple[Any, ...]:
    return (value,)


def wrap_in_tuple(value: Any) -> tuple[Any, ...]:
    return ((value,),)


def wrap_in_list(value: Any) -> tuple[Any, ...]:
    return ([value],)


def wrap_in_dict(value: Any) -> dict[str, Any]:
    return {"a": (value,)}


def wrap_in_set(value: Any) -> tuple[Any, ...]:
    return ({value},)


class SurrogateText(str):
    pass


class SurrogateInt(int):
    def __str__(self) -> str:
        return chr(0xDC80)


class SurrogateObject:
    """A type with no encoder anywhere whose `str()` is a lone surrogate."""

    def __str__(self) -> str:
        return chr(0xDC80)
