"""`execute_many` planning (`_plan_batch`) against fakes: the statements it would send."""

from __future__ import annotations

from typing import Any

from tests.support.mysql_binary import FakeRaw

LIMIT = 64 * 1024  # the documented size a rewritten INSERT is split at
INSERT = "INSERT INTO t (a) VALUES (%s)"
LONE_SURROGATES = [chr(0xDC80), chr(0xDCFF), "a" + chr(0xD800) + "b"]  # built, not written as text


def plan(
    sql: str, batch: list[Any], *, no_backslash_escapes: bool = False, strip: bool = False
) -> list[str]:
    """The statements `execute_many(sql, batch)` would run, decoded."""
    from asyncopation.drivers.mysql.connection import _plan_batch

    raw = FakeRaw(no_backslash_escapes=no_backslash_escapes)
    texts = [statement.decode() for statement in _plan_batch(raw, sql, batch)]
    return [text.strip() for text in texts] if strip else texts


def chunk_count_with_last_statement_longer_by(extra: int) -> int:
    """Number of statements for two rows where the one statement they could share is `extra`
    bytes past the limit (0: exactly at the limit)."""
    head = "INSERT INTO t (a) VALUES "
    second = "y"
    first_length = LIMIT + extra - len(head) - len("(y)") - 1 - 2
    return len(plan(INSERT, [("x" * first_length,), (second,)]))


def statement_lengths_with_one_oversized_row() -> list[int]:
    """Statement lengths for [small, oversized, small]: the oversized row travels alone."""
    statements = plan(INSERT, [("a",), ("b" * (LIMIT + 10),), ("c",)])
    return [len(s) for s in statements]


def planning_seconds_for_a_long_run_of_blanks(blanks: int) -> float:
    """Seconds `_plan_batch` takes on `INSERT ... VALUES (%s)` + blanks + a stray character."""
    import time

    started = time.perf_counter()
    plan("INSERT INTO t VALUES (%s)" + " " * blanks + "x", [(1,)])
    return time.perf_counter() - started


def plan_generator(rows: list[Any]) -> list[str]:
    """`plan` with the rows given as a one-shot generator."""
    from asyncopation.drivers.mysql.connection import _plan_batch

    raw = FakeRaw(no_backslash_escapes=False)
    return [s.decode() for s in _plan_batch(raw, INSERT, (row for row in rows))]
