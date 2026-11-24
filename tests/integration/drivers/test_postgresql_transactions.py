"""begin / commit / rollback / savepoints and what other sessions see."""

from tests.support.drivers import (
    POSTGRES_ONLY,
    in_transaction,
    insert_named,
    raw_begin_insert_exit,
    scratch_count,
    scratch_names,
    scratch_rows,
)

pytestmark = POSTGRES_ONLY


async def test_uncommitted_rows_are_invisible_to_other_sessions(driver, scratch):
    # given
    async with driver.connection() as conn:
        await conn.begin()
        # when
        await insert_named(conn, scratch, "a")
        visible = await scratch_count(scratch)
        await conn.rollback()
    # then
    assert visible == 0


async def test_commit_makes_rows_visible_to_other_sessions(driver, scratch):
    # given
    async with driver.connection() as conn:
        await conn.begin()
        await insert_named(conn, scratch, "a")
        # when
        await conn.commit()
    # then
    assert await scratch_names(scratch) == ["a"]


async def test_rollback_discards_rows(driver, scratch):
    # given
    async with driver.connection() as conn:
        await conn.begin()
        await insert_named(conn, scratch, "a")
        # when
        await conn.rollback()
    # then
    assert await scratch_count(scratch) == 0


async def test_transaction_sees_its_own_uncommitted_rows(driver, scratch):
    # given
    async with driver.connection() as conn:
        await conn.begin()
        await insert_named(conn, scratch, "a")
        # when
        rows = await scratch_rows(conn, scratch)
        await conn.rollback()
    # then
    assert rows == [("a", 0)]


async def test_connection_is_in_transaction_after_begin(driver):
    # given
    async with driver.connection() as conn:
        await conn.begin()
        # when
        inside = await in_transaction(conn)
        await conn.rollback()
    # then
    assert inside is True


async def test_connection_returns_to_autocommit_after_commit(driver, scratch):
    # given
    async with driver.connection() as conn:
        await conn.begin()
        await conn.commit()
        # when
        await insert_named(conn, scratch, "a")
    # then
    assert await scratch_names(scratch) == ["a"]


async def test_fresh_connection_is_not_in_a_transaction(driver):
    # given
    async with driver.connection() as conn:
        # when
        inside = await in_transaction(conn)
    # then
    assert inside is False


async def test_rollback_to_savepoint_discards_later_rows_only(driver, scratch):
    # given
    async with driver.connection() as conn:
        await conn.begin()
        await insert_named(conn, scratch, "a")
        await conn.savepoint("sp_1")
        await insert_named(conn, scratch, "b")
        # when
        await conn.rollback_to_savepoint("sp_1")
        await conn.commit()
    # then
    assert await scratch_names(scratch) == ["a"]


async def test_release_savepoint_keeps_later_rows(driver, scratch):
    # given
    async with driver.connection() as conn:
        await conn.begin()
        await conn.savepoint("sp_1")
        await insert_named(conn, scratch, "b")
        # when
        await conn.release_savepoint("sp_1")
        await conn.commit()
    # then
    assert await scratch_names(scratch) == ["b"]


async def test_nested_savepoints_roll_back_independently(driver, scratch):
    # given
    async with driver.connection() as conn:
        await conn.begin()
        await conn.savepoint("outer")
        await insert_named(conn, scratch, "a")
        await conn.savepoint("inner")
        await insert_named(conn, scratch, "b")
        # when
        await conn.rollback_to_savepoint("inner")
        await conn.release_savepoint("outer")
        await conn.commit()
    # then
    assert await scratch_names(scratch) == ["a"]


async def test_returning_connection_inside_transaction_rolls_it_back(single_driver, scratch):
    # given
    async with single_driver.connection() as first:
        await first.begin()
        await insert_named(first, scratch, "a")
    # when
    async with single_driver.connection() as second:
        rows = await scratch_rows(second, scratch)
        inside = await in_transaction(second)
    # then
    assert rows == []
    assert inside is False
    assert await scratch_count(scratch) == 0


async def test_raw_begin_released_normally_does_not_leak_rows_to_the_next_borrower(
    single_driver, scratch
):
    # when
    rows, _ = await raw_begin_insert_exit(single_driver, scratch)
    # then
    assert rows == []


async def test_raw_begin_released_normally_hands_back_a_connection_outside_a_transaction(
    single_driver, scratch
):
    # when
    _, inside = await raw_begin_insert_exit(single_driver, scratch)
    # then
    assert inside is False
