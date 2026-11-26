"""Errno differential: the same server error raised through mysqlclient (Django) and the driver."""

from __future__ import annotations

from collections.abc import Iterable

from asgiref.sync import sync_to_async

from tests.support.mysql_env import RUN_ID, admin_query, open_my_driver
from tests.support.mysql_reference import real_wrapper

PROCEDURE = f"asyncopation_signal_{RUN_ID}"

# Chunks of the sweep, each well under 10 s. 1900-2100 holds MariaDB specific codes and the
# client side CR_* codes (2000-2100). Exclusions: none so far. A code may legitimately differ for a
# reason other than the class table (a code that makes one library close the connection, say); it
# must be named in EXCLUDED_ERRNOS with the reason instead of being dropped from a range.
CHUNKS = [(1000, 1300), (1300, 1600), (1600, 1900), (1900, 2100), (3000, 3600), (3600, 4200)]
EXCLUDED_ERRNOS: dict[int, str] = {}


async def create_signal_procedure() -> str:
    await admin_query(f"DROP PROCEDURE IF EXISTS {PROCEDURE}")
    await admin_query(
        f"CREATE PROCEDURE {PROCEDURE}(IN e INT) BEGIN "
        "SIGNAL SQLSTATE 'HY000' SET MYSQL_ERRNO = e, MESSAGE_TEXT = 'x'; END"
    )
    return PROCEDURE


async def drop_signal_procedure() -> None:
    await admin_query(f"DROP PROCEDURE IF EXISTS {PROCEDURE}")


async def driver_class_names(codes: Iterable[int]) -> dict[int, str]:
    """Class name of the exception the driver raises for each errno."""
    names: dict[int, str] = {}
    async with open_my_driver(pool={"min_size": 1, "max_size": 2}) as driver:
        for code in codes:
            try:
                async with driver.connection() as conn:
                    await conn.execute(f"CALL {PROCEDURE}({code})", None)
            except Exception as exc:
                names[code] = type(exc).__name__
    return names


async def django_class_names(codes: Iterable[int]) -> dict[int, str]:
    """Class name of the exception Django's mysqlclient cursor raises for each errno."""

    def run() -> dict[int, str]:
        names: dict[int, str] = {}
        wrapper = real_wrapper()
        try:
            for code in codes:
                try:
                    with wrapper.cursor() as cursor:
                        cursor.execute(f"CALL {PROCEDURE}({code})")
                except Exception as exc:
                    names[code] = type(exc).__name__
                    wrapper.close_if_unusable_or_obsolete()
        finally:
            wrapper.close()
        return names

    return await sync_to_async(run)()


def differences(
    ours: dict[int, str], theirs: dict[int, str]
) -> dict[int, tuple[str | None, str | None]]:
    """errno -> (driver class, Django class) wherever they differ."""
    return {
        code: (ours.get(code), theirs.get(code))
        for code in sorted(set(ours) | set(theirs))
        if ours.get(code) != theirs.get(code)
    }


async def sweep_differences(start: int, stop: int) -> dict[int, tuple[str | None, str | None]]:
    """Differences between the driver and mysqlclient for errnos `start <= code < stop`."""
    codes = [code for code in range(start, stop) if code not in EXCLUDED_ERRNOS]
    return differences(await driver_class_names(codes), await django_class_names(codes))


async def classes_for(code: int) -> tuple[str | None, str | None]:
    """(driver class, Django class) for one errno."""
    ours = await driver_class_names([code])
    theirs = await django_class_names([code])
    return ours.get(code), theirs.get(code)


async def connection_replaced_after(code: int) -> tuple[str | None, int, int]:
    """(driver class name, thread id before, thread id of the next borrow) for one errno."""
    from tests.support.mysql_env import connection_id

    async with open_my_driver(pool={"min_size": 1, "max_size": 1}) as driver:
        error = None
        async with driver.connection() as conn:
            before = await connection_id(conn)
            try:
                await conn.execute(f"CALL {PROCEDURE}({code})", None)
            except Exception as exc:
                error = type(exc).__name__
        async with driver.connection() as conn:
            after = await connection_id(conn)
    return error, before, after
