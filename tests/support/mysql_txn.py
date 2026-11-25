"""Transactions the server ends by itself (deadlock, DDL implicit commit): driver vs Django.

Every scenario cleans up in `finally`: a lock left behind by a failing run would hang the scratch
table's `DROP` for the rest of the session.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any

from asgiref.sync import sync_to_async
from django.db import connections

from tests.support.aio import collect, wait_until
from tests.support.mysql_env import (
    admin_query,
    connection_id,
    insert_named,
    open_my_driver,
    scratch_names,
)
from tests.support.mysql_reference import real_wrapper

LOCK_ROW_SQL = "SELECT name FROM {t} WHERE name = %s FOR UPDATE"


@dataclass(frozen=True)
class EndedTransaction:
    survivors: list[str]
    same_connection: bool = True
    error_code: Any = None
    lock_error: Any = (
        None  # errno another connection gets when it updates the row we select FOR UPDATE
    )
    autocommit_after: Any = None  # `@@autocommit` after the transaction ended
    autocommit_flag_off: bool | None = None  # the connection's `_autocommit_off` at the end
    autocommit_before: Any = None  # `@@autocommit` while the server-ended transaction was resumed
    flag_inside: bool | None = None  # `_autocommit_off` while it was resumed


def _ddl_table(table: str) -> str:
    return f"{table}_ddl"


async def _lock_row(conn: Any, entry: str, table: str, name: str) -> None:
    """`SELECT ... FOR UPDATE` through `execute`, `fetch_all`, `execute_many` or `stream`."""
    sql = LOCK_ROW_SQL.format(t=table)
    if entry == "execute":
        await conn.execute(sql, (name,))
    elif entry == "fetch_all":
        await conn.fetch_all(sql, (name,))
    elif entry == "execute_many":
        await conn.execute_many(sql, [(name,)])
    else:
        async with conn.stream(sql, (name,), 1) as chunks:
            await collect(chunks)


async def _new_connection() -> Any:
    import asyncmy

    cfg = connections["default"].settings_dict
    return await asyncmy.connect(
        host=cfg["HOST"],
        port=int(cfg["PORT"]),
        user=cfg["USER"],
        password=cfg["PASSWORD"],
        database=cfg["NAME"],
        autocommit=True,
    )


async def _update_code_from_another_connection(table: str, name: str) -> Any:
    """Errno another connection gets updating `name` with a 1 s lock wait (None: it succeeded)."""
    other = await _new_connection()
    try:
        async with other.cursor() as cur:
            await cur.execute("SET SESSION innodb_lock_wait_timeout = 1, lock_wait_timeout = 1")
            try:
                await cur.execute(f"UPDATE {table} SET qty = qty + 1 WHERE name = %s", (name,))
            except Exception as exc:
                return exc.args[0]
        return None
    finally:
        await other.ensure_closed()


# --------------------------------------------------------------------------- DDL implicit commit
async def driver_ddl_in_transaction(table: str) -> EndedTransaction:
    """begin, insert a, CREATE TABLE (implicit commit), insert b, savepoint, insert c, roll back
    to the savepoint, roll back: which names are left, and was it one connection."""
    ddl = _ddl_table(table)
    try:
        async with (
            open_my_driver(pool={"min_size": 1, "max_size": 1}) as driver,
            driver.connection() as conn,
        ):
            before = await connection_id(conn)
            await conn.begin()
            await insert_named(conn, table, "a")
            await conn.execute(f"CREATE TABLE {ddl} (i INT)", None)
            await insert_named(conn, table, "b")
            await conn.savepoint("sp")
            await insert_named(conn, table, "c")
            await conn.rollback_to_savepoint("sp")
            rows = await conn.fetch_all(f"SELECT name FROM {table} ORDER BY id", None)
            await conn.rollback()
            same = before == await connection_id(conn)
    finally:
        await admin_query(f"DROP TABLE IF EXISTS {ddl}")
    inside = ["inside:" + r[0] for r in rows]
    return EndedTransaction([*await scratch_names(table), *inside], same)


async def sync_ddl_in_transaction(table: str) -> EndedTransaction:
    """The same script on Django's sync wrapper with `autocommit = False`."""
    ddl = _ddl_table(table)

    def run() -> list[str]:
        wrapper = real_wrapper()
        try:
            wrapper.set_autocommit(False)
            with wrapper.cursor() as cur:
                cur.execute(f"INSERT INTO {table} (name, qty) VALUES ('a', 0)")
                cur.execute(f"CREATE TABLE {ddl} (i INT)")
                cur.execute(f"INSERT INTO {table} (name, qty) VALUES ('b', 0)")
                cur.execute("SAVEPOINT `sp`")
                cur.execute(f"INSERT INTO {table} (name, qty) VALUES ('c', 0)")
                cur.execute("ROLLBACK TO SAVEPOINT `sp`")
                cur.execute(f"SELECT name FROM {table} ORDER BY id")
                inside = [r[0] for r in cur.fetchall()]
            wrapper.rollback()
            return inside
        finally:
            wrapper.close()

    try:
        inside = await sync_to_async(run)()
    finally:
        await admin_query(f"DROP TABLE IF EXISTS {ddl}")
    return EndedTransaction([*await scratch_names(table), *("inside:" + n for n in inside)])


async def driver_ddl_then_row_lock(table: str, entry: str) -> EndedTransaction:
    """After a DDL implicit commit inside `begin()`, a `SELECT ... FOR UPDATE` through `entry`
    must keep its row lock (the new transaction): what does another connection get updating it?"""
    ddl = _ddl_table(table)
    await admin_query(f"INSERT INTO {table} (name, qty) VALUES ('a', 0)")
    try:
        async with (
            open_my_driver(pool={"min_size": 1, "max_size": 1}) as driver,
            driver.connection() as conn,
        ):
            await conn.begin()
            await conn.execute(f"CREATE TABLE {ddl} (i INT)", None)
            await _lock_row(conn, entry, table, "a")
            lock_error = await _update_code_from_another_connection(table, "a")
            flag_inside = conn._autocommit_off
            await conn.rollback()
            autocommit = (await conn.fetch_all("SELECT @@autocommit", None))[0][0]
            return EndedTransaction(
                [],
                True,
                None,
                lock_error,
                autocommit,
                conn._autocommit_off,
                flag_inside=flag_inside,
            )
    finally:
        await admin_query(f"DROP TABLE IF EXISTS {ddl}")


async def sync_ddl_then_row_lock(table: str) -> EndedTransaction:
    """The same on Django's sync wrapper: the errno another connection gets."""
    ddl = _ddl_table(table)
    await admin_query(f"INSERT INTO {table} (name, qty) VALUES ('a', 0)")
    holder: dict[str, Any] = {}

    def first() -> None:
        wrapper = holder["w"] = real_wrapper()  # created in the thread that uses it
        wrapper.set_autocommit(False)
        with wrapper.cursor() as cur:
            cur.execute(f"CREATE TABLE {ddl} (i INT)")
            cur.execute(LOCK_ROW_SQL.format(t=table), ("a",))
            cur.fetchall()

    def last() -> None:
        wrapper = holder.get("w")
        if wrapper is None:
            return
        try:
            wrapper.rollback()
        finally:
            wrapper.close()

    try:
        await sync_to_async(first)()
        lock_error = await _update_code_from_another_connection(table, "a")
    finally:
        await sync_to_async(last)()
        await admin_query(f"DROP TABLE IF EXISTS {ddl}")
    return EndedTransaction([], lock_error=lock_error)


# --------------------------------------------------------------------------- LOCK TABLES
async def _insert_code_while_tables_locked(table: str) -> Any:
    other = await _new_connection()
    try:
        async with other.cursor() as cur:
            await cur.execute("SET SESSION innodb_lock_wait_timeout = 1, lock_wait_timeout = 1")
            try:
                await cur.execute(f"INSERT INTO {table} (name, qty) VALUES ('other', 0)")
            except Exception as exc:
                return exc.args[0]
        return None
    finally:
        await other.ensure_closed()


async def driver_lock_tables_in_transaction(table: str) -> EndedTransaction:
    """`LOCK TABLES` inside `begin()`, a statement after it: is the lock still held?"""
    async with (
        open_my_driver(pool={"min_size": 1, "max_size": 1}) as driver,
        driver.connection() as conn,
    ):
        await conn.begin()
        try:
            await conn.execute(f"LOCK TABLES {table} WRITE", None)
            await conn.fetch_all(f"SELECT COUNT(*) FROM {table}", None)
            code = await _insert_code_while_tables_locked(table)
        finally:
            await conn.execute("UNLOCK TABLES", None)
            await conn.rollback()
            await admin_query(f"DELETE FROM {table} WHERE name = 'other'")
        autocommit = (await conn.fetch_all("SELECT @@autocommit", None))[0][0]
        return EndedTransaction([], lock_error=code, autocommit_after=autocommit)


async def sync_lock_tables_in_transaction(table: str) -> EndedTransaction:
    holder: dict[str, Any] = {}

    def first() -> None:
        wrapper = holder["w"] = real_wrapper()  # created in the thread that uses it
        wrapper.set_autocommit(False)
        with wrapper.cursor() as cur:
            cur.execute(f"LOCK TABLES {table} WRITE")
            cur.execute(f"SELECT COUNT(*) FROM {table}")
            cur.fetchall()

    def last() -> None:
        wrapper = holder.get("w")
        if wrapper is None:
            return
        try:
            with wrapper.cursor() as cur:
                cur.execute("UNLOCK TABLES")
            wrapper.rollback()
        finally:
            wrapper.close()

    try:
        await sync_to_async(first)()
        code = await _insert_code_while_tables_locked(table)
    finally:
        await sync_to_async(last)()
    return EndedTransaction([], lock_error=code)


# --------------------------------------------------------------------------- autocommit restore
async def autocommit_after_ending(table: str, how: str) -> EndedTransaction:
    """After a DDL implicit commit (the connection is on `autocommit = 0`) end the transaction
    with `commit()`, `rollback()` or by returning the connection; look at the next borrow."""
    ddl = _ddl_table(table)
    driver_cm = open_my_driver(pool={"min_size": 1, "max_size": 1})
    try:
        async with driver_cm as driver:
            borrow = driver.connection()
            conn = await borrow.__aenter__()
            before = await connection_id(conn)
            await conn.begin()
            await conn.execute(f"CREATE TABLE {ddl} (i INT)", None)
            await conn.execute(f"INSERT INTO {table} (name, qty) VALUES ('x', 0)", None)
            inside = (await conn.fetch_all("SELECT @@autocommit", None))[0][0]
            if how == "commit":
                await conn.commit()
            elif how == "rollback":
                await conn.rollback()
            flag = conn._autocommit_off
            if how == "release":
                await borrow.__aexit__(None, None, None)
                async with driver.connection() as again:
                    autocommit = (await again.fetch_all("SELECT @@autocommit", None))[0][0]
                    same = before == await connection_id(again)
                    flag = again._autocommit_off
            else:
                autocommit = (await conn.fetch_all("SELECT @@autocommit", None))[0][0]
                same = before == await connection_id(conn)
                await borrow.__aexit__(None, None, None)
            return EndedTransaction([], same, None, None, autocommit, flag, inside)
    finally:
        await admin_query(f"DROP TABLE IF EXISTS {ddl}")


# --------------------------------------------------------------------------- deadlock
async def _until_blocked(blocked_thread: int) -> None:
    """Wait until the server shows `blocked_thread` waiting for a lock."""

    async def waiting() -> bool:
        rows = await admin_query(
            "SELECT COUNT(*) FROM information_schema.innodb_trx "
            "WHERE trx_state = 'LOCK WAIT' AND trx_mysql_thread_id = %s",
            (blocked_thread,),
        )
        return bool(rows[0][0])

    await wait_until(waiting, within=10.0)


async def _hold_row_two_and_wait_for_row_one(other: Any, table: str) -> asyncio.Task[None]:
    """B: BEGIN, update r2, then block on r1 (held by the victim)."""
    async with other.cursor() as cur:
        await cur.execute("BEGIN")
        await cur.execute(f"UPDATE {table} SET qty = qty + 1 WHERE name = 'r2'")

    async def blocked_update() -> None:
        async with other.cursor() as cur:
            await cur.execute(f"UPDATE {table} SET qty = qty + 1 WHERE name = 'r1'")

    task = asyncio.create_task(blocked_update())
    try:
        await _until_blocked(other.thread_id())
    except BaseException:
        task.cancel()
        await asyncio.wait([task])
        raise
    return task


async def _finish_other(other: Any, task: asyncio.Task[None] | None) -> None:
    """Let B end (its blocked statement may still be pending), roll back, close."""
    try:
        if task is not None:
            await asyncio.wait([task], timeout=15)
            task.cancel()
            await asyncio.wait([task])
        if other.connected:  # a dropped connection must not hide the original error
            async with other.cursor() as cur:
                await cur.execute("ROLLBACK")
    finally:
        await other.ensure_closed()


async def _late_rows(table: str) -> list[str]:
    rows = await admin_query(f"SELECT name FROM {table} WHERE name = 'late'")
    await admin_query(f"DELETE FROM {table} WHERE name = 'late'")
    return [r[0] for r in rows]


async def driver_deadlock_victim(
    table: str, entry: str = "execute", victim: str = "execute"
) -> EndedTransaction:
    """The driver's transaction is the deadlock victim; the caller swallows the error, then runs
    `entry` (a `SELECT ... FOR UPDATE` on r1, whose lock shows whether a transaction is open),
    inserts `late` and rolls back: does `late` survive, is the row lock held?"""
    await admin_query(f"INSERT INTO {table} (name, qty) VALUES ('r1', 0), ('r2', 0)")
    other = await _new_connection()
    task: asyncio.Task[None] | None = None
    finished = False
    try:
        async with (
            open_my_driver(pool={"min_size": 1, "max_size": 1}) as driver,
            driver.connection() as conn,
        ):
            before = await connection_id(conn)
            await conn.begin()
            await conn.execute(f"UPDATE {table} SET qty = qty + 1 WHERE name = 'r1'", None)
            task = await _hold_row_two_and_wait_for_row_one(other, table)
            code = None
            try:
                if victim == "execute_many":  # the second statement of the batch is the victim
                    sql = f"UPDATE {table} SET qty = qty + 1 WHERE name = %s"
                    await conn.execute_many(sql, [("nobody",), ("r2",)])
                else:
                    await conn.execute(f"UPDATE {table} SET qty = qty + 1 WHERE name = 'r2'", None)
            except Exception as exc:
                code = exc.args[0]
            await _finish_other(other, task)
            finished = True
            await _lock_row(conn, entry, table, "r1")
            lock_error = await _update_code_from_another_connection(table, "r1")
            await insert_named(conn, table, "late")
            await conn.rollback()
            same = before == await connection_id(conn)
    finally:
        if not finished:
            await _finish_other(other, task)
    return EndedTransaction(await _late_rows(table), same, code, lock_error)


async def sync_deadlock_victim(table: str) -> EndedTransaction:
    """The same script on Django's sync wrapper (autocommit off): survivors of `late`, and
    whether the row lock of a later `SELECT ... FOR UPDATE` is held."""
    await admin_query(f"INSERT INTO {table} (name, qty) VALUES ('r1', 0), ('r2', 0)")
    other = await _new_connection()
    holder: dict[str, Any] = {}
    task: asyncio.Task[None] | None = None
    finished = False

    def first() -> None:
        wrapper = real_wrapper()
        wrapper.set_autocommit(False)
        holder["w"] = wrapper
        with wrapper.cursor() as cur:
            cur.execute(f"UPDATE {table} SET qty = qty + 1 WHERE name = 'r1'")

    def victim() -> Any:
        try:
            with holder["w"].cursor() as cur:
                cur.execute(f"UPDATE {table} SET qty = qty + 1 WHERE name = 'r2'")
        except Exception as exc:
            return exc.args[0]
        return None

    def lock_row() -> None:
        with holder["w"].cursor() as cur:
            cur.execute(LOCK_ROW_SQL.format(t=table), ("r1",))
            cur.fetchall()

    def last() -> None:
        wrapper = holder.get("w")
        if wrapper is None:
            return
        try:
            with wrapper.cursor() as cur:
                cur.execute(f"INSERT INTO {table} (name, qty) VALUES ('late', 0)")
            wrapper.rollback()
        finally:
            wrapper.close()

    try:
        await sync_to_async(first)()
        task = await _hold_row_two_and_wait_for_row_one(other, table)
        code = await sync_to_async(victim)()
        await _finish_other(other, task)
        finished = True
        await sync_to_async(lock_row)()
        lock_error = await _update_code_from_another_connection(table, "r1")
    finally:
        if not finished:
            await _finish_other(other, task)
        await sync_to_async(last)()
    return EndedTransaction(await _late_rows(table), True, code, lock_error)


@dataclass(frozen=True)
class StreamErrorAfterDdl:
    body_error: BaseException | None
    same_connection: bool
    next_rows: list[Any]
    survivors: list[str]


async def stream_body_error_in_a_resumed_transaction(table: str) -> StreamErrorAfterDdl:
    """begin, DDL (implicit commit: the transaction is resumed with autocommit = 0), insert, a
    stream whose body raises (caught), `select 1`, rollback: the connection must have survived."""
    from tests.support.mysql_scenarios import SERIES_SQL, error_of

    ddl = _ddl_table(table)

    async def failing_stream(conn: Any) -> None:
        async with conn.stream(SERIES_SQL, None, 10) as chunks:
            await anext(chunks)
            raise ValueError("body failed")

    try:
        async with (
            open_my_driver(pool={"min_size": 1, "max_size": 1}) as driver,
            driver.connection() as conn,
        ):
            before = await connection_id(conn)
            await conn.begin()
            await conn.execute(f"CREATE TABLE {ddl} (i INT)", None)
            await insert_named(conn, table, "b")
            body_error = await error_of(failing_stream(conn))
            rows = await conn.fetch_all("SELECT 1", None)
            await conn.rollback()
            same = before == await connection_id(conn)
    finally:
        await admin_query(f"DROP TABLE IF EXISTS {ddl}")
    return StreamErrorAfterDdl(body_error, same, rows, await scratch_names(table))
