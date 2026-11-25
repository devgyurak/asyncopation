"""The registry's list of open pinned executors, which `close()` marks before closing drivers."""

from tests.support.executors.close_pins import (
    close_pins_after_unregistering,
    close_pins_of_another_loop,
    close_pins_twice,
    close_registered_pins,
    registered_pin_is_kept_alive,
    unregister_a_pin_that_was_never_registered,
)


async def test_close_pins_marks_every_pin_registered_for_the_loop(fake):
    # when
    marks = await close_registered_pins("default", "other", "default")
    # then
    assert marks == [1, 1, 1]


async def test_close_pins_skips_a_pin_that_was_unregistered(fake):
    # when
    marked = await close_pins_after_unregistering()
    # then
    assert marked == 0


async def test_close_pins_leaves_the_pins_of_another_loop_alone(fake):
    # when
    marked = await close_pins_of_another_loop()
    # then
    assert marked == 0


async def test_close_pins_marks_a_pin_only_once(fake):
    # when
    marked = await close_pins_twice()
    # then
    assert marked == 1


async def test_the_registry_does_not_keep_a_pin_alive(fake):
    # when
    alive = await registered_pin_is_kept_alive()
    # then
    assert alive is False


async def test_unregistering_a_pin_that_was_never_registered_is_harmless(fake):
    # when
    result = await unregister_a_pin_that_was_never_registered()
    # then
    assert result is None
