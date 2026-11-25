"""Table-driven differential of parameter rendering: driver vs mysqlclient, per value and shape.

Every value runs at the top level, nested in a tuple, in a list, and as a dict parameter, and the
outcomes (rows, or the error class) of the driver and of Django's sync backend must be equal.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from decimal import Decimal
from enum import Enum
from typing import Any
from uuid import UUID

from tests.support.mysql_binary import (
    FrozenSubclass,
    Point,
    QuotedDecimal,
    QuotedFloat,
    QuotedInt,
    TextChoice,
    TupleSubclass,
    Weird,
)
from tests.support.mysql_reference import compare_outcomes


class FloatChoice(float, Enum):
    HALF = 0.5


class PlainNaN(Decimal):
    """A Decimal subclass whose `str()` is 'NaN'."""


VALUES: dict[str, Any] = {
    "none": None,
    "int": 7,
    "bool": True,
    "float": 1.5,
    "nan": float("nan"),
    "inf": float("inf"),
    "neg-inf": float("-inf"),
    "decimal": Decimal("1.50"),
    "str-quotes": "a'b\"c",
    "str-backslash": "a\\b\\'",
    "bytes": b"a'b\\c\x00",
    "bytearray": bytearray(b"a'b"),
    "memoryview": memoryview(b"a'b"),
    "date": date(2024, 2, 29),
    "datetime": datetime(2024, 2, 29, 1, 2, 3, 4),
    "time": time(1, 2, 3),
    "timedelta": timedelta(seconds=90),
    "uuid": UUID(int=5),
    "set-of-str": {"a", "b'c"},
    "frozenset-of-str": frozenset({"q\\"}),
    "empty-set": set(),
    "set-of-int": {1},
    "set-subclass": FrozenSubclass({"a"}),
    "namedtuple": Point(1, 2),
    "tuple-subclass": TupleSubclass((1, 2)),
    "list-in-list": [1, [2, "x'"]],
    "set-in-list": [{"a"}],
    "int-subclass": QuotedInt(5),
    "float-subclass": QuotedFloat(1.5),
    "decimal-subclass": QuotedDecimal("1.5"),
    "decimal-nan-subclass": PlainNaN("NaN"),
    "float-enum": FloatChoice.HALF,
    "str-enum": TextChoice.A,
    "set-of-str-enum": {TextChoice.A},
    "weird": Weird(),
}


# Documented differences from mysqlclient: the driver's outcome must then be one of these.
# - bytes / bytearray echoed as a `bytes` column where mysqlclient decodes to `str` (the same
#   bytes; asyncmy's `_binary'..'` literal also makes comparisons binary, see ADR-0003).
# - memoryview: mysqlclient sends the text '<memory at 0x..>', a bug; the driver sends the bytes.
# - timedelta: '00:01:30' against '0 0:1:30', the same TIME value.
_BYTES = ("rows", [(("bytes", repr(b"a'b\\c\x00")),)])
_BYTEARRAY = ("rows", [(("bytes", repr(b"a'b")),)])
_TEXT = ("rows", [(("str", repr("a'b")),)])
_TIME = ("rows", [(("str", repr("00:01:30")),)])
ALLOWED_OUTCOMES: dict[str, list[Any]] = {
    "bytes": [_BYTES],
    "bytearray": [_BYTEARRAY],
    "memoryview": [_BYTEARRAY, _TEXT],
    "timedelta": [_TIME],
}
ECHO_SHAPES = ("top", "tuple", "list", "dict", "dict-tuple")


def expected_differences(hex_mode: bool) -> set[tuple[str, str]]:
    """(value, shape) pairs whose outcome differs from mysqlclient's. Inside a sequence `bytes`
    are a plain quoted literal in the default mode (as mysqlclient), `X'..'` under
    NO_BACKSLASH_ESCAPES; at the top level and as a dict value asyncmy's `_binary'..'` stays."""
    bytes_shapes = ECHO_SHAPES if hex_mode else ("top", "dict")
    pairs = {(name, shape) for name in ("bytes", "bytearray") for shape in bytes_shapes}
    pairs |= {(name, shape) for name in ("memoryview", "timedelta") for shape in ECHO_SHAPES}
    return pairs


def render_cases() -> list[tuple[str, str, str, Any]]:
    """(value name, shape, sql, params) for every value and shape; each one echoes the value back."""
    cases: list[tuple[str, str, str, Any]] = []
    for name, value in VALUES.items():
        cases.append((name, "top", "SELECT %s", [value]))
        cases.append((name, "tuple", "SELECT %s", [(value,)]))
        cases.append((name, "list", "SELECT %s", [[value]]))
        cases.append((name, "dict", "SELECT %(a)s", {"a": value}))
        cases.append((name, "dict-tuple", "SELECT %(a)s", {"a": (value,)}))
        cases.append((name, "in", "SELECT %s IN %s", [value, (value,)]))
    return cases


async def render_mismatches(options: dict[str, Any], *, hex_mode: bool) -> list[Any]:
    """Every case whose outcome differs from mysqlclient's when it should not, does not when it
    should, or differs in a way other than the documented one."""
    expected = expected_differences(hex_mode)
    problems: list[Any] = []
    for name, shape, sql, params in render_cases():
        result = await compare_outcomes(sql, params, options=options)
        differs = result.async_outcome != result.sync_outcome
        if differs != ((name, shape) in expected):
            problems.append((name, shape, result.async_outcome, result.sync_outcome))
        elif differs and result.async_outcome not in ALLOWED_OUTCOMES[name]:
            problems.append((name, shape, "undocumented outcome", result.async_outcome))
    return problems
