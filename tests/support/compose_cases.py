"""`compose_sql` cases: supported ones carry the text live Django produces; the rest must raise."""

from __future__ import annotations

import datetime
import decimal
import enum
import uuid

import pytest
from django.db.backends.postgresql.psycopg_any import Jsonb  # ty: ignore[import-not-found]
from django.db.models import IntegerChoices, TextChoices
from psycopg.types.range import Range

UTC = datetime.UTC


class Number(enum.IntEnum):
    THREE = 3


class Fruit(enum.StrEnum):
    APPLE = "apple"


class Color(TextChoices):
    RED = "red"


class Rank(IntegerChoices):
    FIRST = 1


class BigNumber(enum.IntEnum):
    BIG = 2**63


class ListSub(list):
    """A list whose `__len__` and `__iter__` disagree."""

    def __len__(self) -> int:
        return 5


class TupleSub(tuple):
    """A tuple subclass."""


class FloatSub(float):
    """A float subclass."""


class Text(str):
    """A str subclass."""


class Letter(str, enum.Enum):  # noqa: UP042 - the (str, Enum) mixin is the case under test
    A = "it's"


class Count(int):
    """An int subclass."""


class Money(decimal.Decimal):
    """A Decimal subclass."""


class Spelled(decimal.Decimal):
    """A Decimal subclass whose `__str__` lies; compose_sql rejects it."""

    def __str__(self) -> str:
        return "custom-str"


class DecimalChoice(decimal.Decimal, enum.Enum):  # ty: ignore[lost-keyword-for-regular-parameter]
    """A Decimal enum like Django's `models.Choices` mixin: `str()` is the member name."""

    A = "1.50"


class AlwaysFinite(decimal.Decimal):
    """A Decimal subclass whose `is_finite` lies (always True)."""

    def is_finite(self) -> bool:
        return True


class NeverFinite(decimal.Decimal):
    """A Decimal subclass whose `is_finite` lies (always False)."""

    def is_finite(self) -> bool:
        return False


class ICmp(int):
    """An int subclass whose comparisons make every number look negative (psycopg tests `>= 0`)."""

    def __lt__(self, other: object) -> bool:
        return True

    def __ge__(self, other: object) -> bool:
        return False


class IntDunder(int):
    """An int subclass whose `__int__` lies; the renderer must use the value."""

    def __int__(self) -> int:
        return 99


class Replacing(str):
    """A str subclass whose `replace` lies; the renderer must not call it."""

    def replace(self, *args, **kwargs):  # type: ignore[override]
        return "BAD"


class Chatty(str):
    """A str subclass whose text representations differ from its value."""

    def __str__(self) -> str:
        return "custom-str"

    def __repr__(self) -> str:
        return "custom-repr"


class ChattyCount(int):
    """An int subclass whose text representations differ from its value."""

    def __str__(self) -> str:
        return "custom-str"

    def __repr__(self) -> str:
        return "custom-repr"


P = pytest.param

# (sql, params, expected) - `expected` was copied from a live PostgreSQL run of Django's own
# `ops.compose_sql`; the integration test asserts the equality again on every run.
COMPOSE_CASES = [
    P("%s", ("it's",), "'it''s'", id="single-quote"),
    P("%s", ("back\\slash",), " E'back\\\\slash'", id="backslash"),
    P("%s", ("a\\'b",), " E'a\\\\''b'", id="backslash-and-quote"),
    P("%s", ("a\nb",), "'a\nb'", id="newline"),
    P("%s", ("héllo ✓",), "'héllo ✓'", id="unicode"),
    P("%s", ("a%b",), "'a%b'", id="percent-in-value"),
    P("%s", ('say "hi"',), "'say \"hi\"'", id="double-quote"),
    P("%s", ("",), "''", id="empty-string"),
    P("%s", (0,), "0", id="int-zero"),
    P("%s", (5,), "5", id="int"),
    P("%s", (-5,), " -5", id="negative-int"),
    P("%s", (2**31,), "2147483648", id="int-2-31"),
    P("%s", (2**63,), "9223372036854775808", id="int-2-63"),
    P("%s", (-(2**63) - 1,), " -9223372036854775809", id="int-below-int64"),
    P("%s", (2**70,), "1180591620717411303424", id="big-int"),
    P("%s", (-(2**70),), " -1180591620717411303424", id="big-negative-int"),
    P("%s", (1.5,), "1.5", id="float"),
    P("%s", (-2.5,), " -2.5", id="negative-float"),
    P("%s", (0.5,), "0.5", id="float-half"),
    P("%s", (0.0,), "0.0", id="float-zero"),
    P("%s", (-0.0,), " -0.0", id="negative-zero-float"),
    P("%s", (1e16,), "1e+16", id="float-1e16"),
    P("%s", (1e-7,), "1e-07", id="float-small"),
    P("%s", (5e-324,), "5e-324", id="float-denormal"),
    P("%s", (1.7976931348623157e308,), "1.7976931348623157e+308", id="float-max"),
    P("%s", (float("inf"),), "'Infinity'::float8", id="float-infinity"),
    P("%s", (float("-inf"),), "'-Infinity'::float8", id="float-negative-infinity"),
    P("%s", (float("nan"),), "'NaN'::float8", id="float-nan"),
    P("%s", (True,), "true", id="true"),
    P("%s", (False,), "false", id="false"),
    P("%s", (None,), "NULL", id="none"),
    P("%s", (decimal.Decimal(5),), "5", id="decimal-int"),
    P("%s", (decimal.Decimal(-5),), " -5", id="decimal-negative"),
    P("%s", (decimal.Decimal("1.50"),), "1.50", id="decimal-trailing-zero"),
    P("%s", (decimal.Decimal("-0.0"),), " -0.0", id="decimal-negative-zero-fraction"),
    P("%s", (decimal.Decimal("0.00"),), "0.00", id="decimal-zero-scale"),
    P("%s", (decimal.Decimal("1E+3"),), "1E+3", id="decimal-positive-exponent"),
    P("%s", (decimal.Decimal("1E-7"),), "1E-7", id="decimal-negative-exponent"),
    P("%s", (decimal.Decimal("-1.5E+10"),), " -1.5E+10", id="decimal-negative-scientific"),
    P(
        "%s",
        (decimal.Decimal("123456789012345678901234567890.123456789"),),
        "123456789012345678901234567890.123456789",
        id="decimal-30-digits",
    ),
    P("%s", (decimal.Decimal("0E+2"),), "0E+2", id="decimal-zero-exponent"),
    P("%s", (decimal.Decimal("-0"),), " -0", id="decimal-negative-zero"),
    P("100%%", (), "100%", id="escaped-percent"),
    P("a %s b %% c %s %s", ("x", -2, None), "a 'x' b % c  -2 NULL", id="multiple-params"),
    P("%s,%s", (1, -5), "1, -5", id="negative-after-comma"),
    P("%s", [1], "1", id="list-params"),
    P("100%%", None, "100%%", id="no-params-keeps-escapes"),
    P("%s", None, "%s", id="no-params-keeps-placeholders"),
]

SUB = "only exact built-in types"

# (sql, params, error name, message fragment): rejected without a connection.
REJECTED_CASES = [
    P("%s", (b"ab",), "TypeError", "cannot render bytes", id="bytes"),
    P("%s", (bytearray(b"ab"),), "TypeError", "cannot render bytearray", id="bytearray"),
    P("%s", (memoryview(b"ab"),), "TypeError", "cannot render memoryview", id="memoryview"),
    P("%s", ([1],), "TypeError", "cannot render list", id="list-value"),
    P("%s", ((1,),), "TypeError", "cannot render tuple", id="tuple-value"),
    P("%s", (AlwaysFinite("NaN"),), "TypeError", SUB, id="decimal-nan-claiming-finite"),
    P("%s", (Spelled("2.50"),), "TypeError", SUB, id="decimal-subclass-with-custom-str"),
    P("%s", (Spelled("-3"),), "TypeError", SUB, id="negative-decimal-subclass-with-custom-str"),
    P("%s", (DecimalChoice.A,), "TypeError", SUB, id="decimal-choices"),
    P("%s", (NeverFinite("1"),), "TypeError", SUB, id="decimal-never-finite"),
    P("%s", (Money("2.50"),), "TypeError", SUB, id="decimal-subclass"),
    P("%s", (IntDunder(5),), "TypeError", SUB, id="int-subclass-overriding-int"),
    P("%s", (ICmp(5),), "TypeError", SUB, id="int-subclass-overriding-comparisons"),
    P("%s", (ICmp(-5),), "TypeError", SUB, id="negative-int-subclass-overriding-comparisons"),
    P("%s", (Number.THREE,), "TypeError", SUB, id="int-enum"),
    P("%s", (BigNumber.BIG,), "TypeError", SUB, id="int-enum-beyond-int64"),
    P("%s", (Letter.A,), "TypeError", SUB, id="str-enum"),
    P("%s", (Fruit.APPLE,), "TypeError", SUB, id="str-enum-strenum"),
    P("%s", (Color.RED,), "TypeError", SUB, id="text-choices"),
    P("%s", (Rank.FIRST,), "TypeError", SUB, id="integer-choices"),
    P("%s", (Text("it's"),), "TypeError", SUB, id="str-subclass"),
    P("%s", (Chatty("it's"),), "TypeError", SUB, id="str-subclass-with-custom-text"),
    P("%s", (Replacing("it's"),), "TypeError", SUB, id="str-subclass-overriding-replace"),
    P("%s", (Count(7),), "TypeError", SUB, id="int-subclass"),
    P("%s", (ChattyCount(7),), "TypeError", SUB, id="int-subclass-with-custom-text"),
    P("%s", (FloatSub(1.5),), "TypeError", SUB, id="float-subclass"),
    P("%s", (decimal.Decimal("NaN"),), "TypeError", "non-finite Decimal", id="decimal-nan"),
    P("%s", (decimal.Decimal("sNaN"),), "TypeError", "non-finite Decimal", id="decimal-snan"),
    P("%s", (decimal.Decimal("Infinity"),), "TypeError", "non-finite Decimal", id="decimal-inf"),
    P("%s", (decimal.Decimal("-Infinity"),), "TypeError", "non-finite Decimal",
      id="decimal-negative-inf"),
    P("%s", (datetime.date(2024, 2, 29),), "TypeError", "cannot render date", id="date"),
    P("%s", (datetime.datetime(2024, 2, 29, 13, 5, 6),), "TypeError", "cannot render datetime",
      id="naive-datetime"),
    P("%s", (datetime.datetime(2024, 2, 29, 13, 5, 6, tzinfo=UTC),), "TypeError",
      "cannot render datetime", id="aware-datetime"),
    P("%s", (datetime.time(13, 5, 6),), "TypeError", "cannot render time", id="time"),
    P("%s", (datetime.timedelta(days=1),), "TypeError", "cannot render timedelta", id="timedelta"),
    P("%s", (uuid.UUID(int=1),), "TypeError", "cannot render UUID", id="uuid"),
    P("%s", (Range(1, 5),), "TypeError", "cannot render Range", id="range"),
    P("%s", (Jsonb({"a": 1}),), "TypeError", "cannot render Jsonb", id="json"),
    P("%s", (i for i in [1]), "TypeError", "must be a list or tuple", id="generator-params"),
    P("%s", {1}, "TypeError", "must be a list or tuple", id="set-params"),
    P("%s", "abc", "TypeError", "must be a list or tuple", id="str-params"),
    P("%s", b"abc", "TypeError", "must be a list or tuple", id="bytes-params"),
    P("%(name)s", {"name": 1}, "TypeError", "named placeholders", id="named-placeholder"),
    P("%b", (1,), "ValueError", "unsupported placeholder %b", id="binary-placeholder"),
    P("%t", (1,), "ValueError", "unsupported placeholder %t", id="text-placeholder"),
    P("%x", (1,), "ValueError", "unsupported placeholder %x", id="unknown-placeholder"),
    P("100%", (), "ValueError", "unsupported placeholder %", id="dangling-percent"),
    P("%s %s", (1,), "ValueError", "not enough parameters", id="too-few-params"),
    P("%s", (1, 2), "ValueError", "too many parameters", id="too-many-params"),
    P("%s", (1, None), "ValueError", "too many parameters", id="too-many-with-none"),
    P("%s", ("a\x00b",), "ValueError", "cannot contain NUL", id="nul-in-string"),
    P("%s", ("\ud800",), "UnicodeEncodeError", "surrogate", id="lone-surrogate"),
    P("%s", ("\x00\ud800",), "UnicodeEncodeError", "surrogate", id="nul-then-surrogate"),
    P("%s", ("\ud83d\ude00",), "UnicodeEncodeError", "surrogate", id="surrogate-pair-like"),
    P("%s", ("\ud800", 1), "ValueError", "too many parameters", id="count-before-value"),
    P("%s %s", ("\ud800",), "ValueError", "not enough parameters", id="too-few-before-value"),
    P("\ud800", None, "UnicodeEncodeError", "surrogate", id="surrogate-in-sql-without-params"),
    P("%s \ud800", (1,), "UnicodeEncodeError", "surrogate", id="surrogate-in-sql-with-params"),
    P("%s", ListSub([1]), "TypeError", "must be a list or tuple", id="list-subclass-params"),
    P("%s", TupleSub((1,)), "TypeError", "must be a list or tuple", id="tuple-subclass-params"),
    P("%s %b", ("\ud800", 1), "ValueError", "unsupported placeholder %b", id="shape-before-value"),
]  # fmt: skip
