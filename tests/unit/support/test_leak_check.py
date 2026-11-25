"""`assert_registry_clean` (the integration autouse leak check) must be able to fail."""

import pytest

from asyncopation.connection import registry
from asyncopation.connection.lifecycle import ensure_driver
from tests.support.lifecycle.checks import assert_registry_clean
from tests.support.lifecycle.loops import leave_open_in_other_loop


async def test_registry_clean_check_passes_when_nothing_is_open(configured):
    # when / then
    await assert_registry_clean()


async def test_registry_clean_check_fails_when_a_driver_is_left_open(configured):
    # given
    await ensure_driver("default")
    # when / then
    with pytest.raises(AssertionError, match="left drivers in the registry"):
        await assert_registry_clean()


async def test_registry_clean_check_fails_when_a_driver_was_dropped_with_a_closed_loop(configured):
    # given
    await leave_open_in_other_loop("default")
    # when / then
    with pytest.raises(AssertionError, match="left drivers in the registry"):
        await assert_registry_clean()


async def test_registry_clean_check_resets_the_registry_even_when_it_fails(configured):
    # given
    await ensure_driver("default")
    with pytest.raises(AssertionError):
        await assert_registry_clean()
    # when
    entries = registry.entries()
    # then
    assert entries == []


async def test_registry_clean_check_closes_the_leftover_driver_before_failing(configured):
    # given
    await ensure_driver("default")
    with pytest.raises(AssertionError):
        await assert_registry_clean()
    # when
    closes = configured.recorder.closes
    # then
    assert closes == 1
