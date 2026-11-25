"""Scenarios about `close()` and the pins it gave up on (ADR-0001 10a, ADR-0004), and about the
registry's list of open pins. Every function only *observes* and returns an outcome."""

from __future__ import annotations

import asyncio
import gc
import weakref
from dataclasses import dataclass
from typing import Any

from tests.support.executors.fakes import ExecEnv
from tests.support.executors.ops import call, kinds_of, raised_by
from tests.support.executors.scenarios import ALIAS, _executors, _registry
from tests.support.lifecycle.loops import fresh_loop


@dataclass(frozen=True)
class AfterGiveUp:
    close_error: BaseException | None
    closed: bool
    error: BaseException | None
    kinds: list[str]
    exit_error: BaseException | None
    in_flight: int
    released: list[bool]


async def use_a_pin_after_close_gave_up(env: ExecEnv, op: str) -> AfterGiveUp:
    """`close(timeout)` gives up on an open pin; the owner then runs `op` and leaves the block."""
    from asyncopation.connection import close

    pin_cm = _executors().pin(ALIAS)
    executor = await pin_cm.__aenter__()
    close_error = await raised_by(close(timeout=0.05))
    closed = executor.closed
    error = await raised_by(call(executor, op))
    kinds = kinds_of(env.default)
    exit_error = await raised_by(pin_cm.__aexit__(None, None, None))
    return AfterGiveUp(
        close_error,
        closed,
        error,
        kinds,
        exit_error,
        _registry().in_flight(ALIAS),
        env.default.released,
    )


class PinSpy:
    """Stands in for a pinned executor: counts `mark_drivers_closed()`."""

    def __init__(self) -> None:
        self.marked = 0

    def mark_drivers_closed(self) -> None:
        self.marked += 1


def _register(alias: str) -> PinSpy:
    spy = PinSpy()
    _registry().register_pin(alias, spy)
    return spy


async def close_registered_pins(*aliases: str) -> list[int]:
    """Register one pin per alias in this loop, then `close_pins` for this loop: marks per pin."""
    spies = [_register(alias) for alias in aliases]
    _registry().close_pins(asyncio.get_running_loop())
    return [spy.marked for spy in spies]


async def close_pins_after_unregistering() -> int:
    spy = _register(ALIAS)
    _registry().unregister_pin(ALIAS, spy)
    _registry().close_pins(asyncio.get_running_loop())
    return spy.marked


async def close_pins_of_another_loop() -> int:
    spy = _register(ALIAS)
    _registry().close_pins(fresh_loop())
    return spy.marked


async def close_pins_twice() -> int:
    spy = _register(ALIAS)
    _registry().close_pins(asyncio.get_running_loop())
    _registry().close_pins(asyncio.get_running_loop())
    return spy.marked


async def registered_pin_is_kept_alive() -> bool:
    """Does registering a pin keep it alive once nothing else refers to it?"""
    reference = weakref.ref(_register(ALIAS))
    gc.collect()
    return reference() is not None


async def unregister_a_pin_that_was_never_registered() -> Any:
    _registry().unregister_pin(ALIAS, PinSpy())


@dataclass(frozen=True)
class OwnerAfterGiveUp:
    current: Any
    pin: Any
    error: BaseException | None
    after_exit: Any


async def select_an_executor_after_close_gave_up_on_the_pin() -> OwnerAfterGiveUp:
    """The owner, still inside its block, asks `current_executor` after `close(timeout)` gave up on
    the pin: it must get the pin (and the error), not a borrowed executor outside its transaction.
    After leaving the block the owner may borrow again."""
    from asyncopation.connection import close

    mod = _executors()
    pin_cm = mod.pin(ALIAS)
    executor = await pin_cm.__aenter__()
    await raised_by(close(timeout=0.05))
    current = mod.current_executor(ALIAS)
    error = await raised_by(current.fetch_all("SELECT 1", None))
    await pin_cm.__aexit__(None, None, None)
    return OwnerAfterGiveUp(current, executor, error, mod.current_executor(ALIAS))


async def pin_again_after_close_gave_up_on_the_pin() -> BaseException | None:
    """The owner opens a nested block of the alias after `close(timeout)` gave up on its pin: it
    must not get an independent pin that would hide the lost one."""
    from asyncopation.connection import close

    mod = _executors()
    async with mod.pin(ALIAS):
        await raised_by(close(timeout=0.05))
        return await raised_by(_enter_and_leave(mod.pin(ALIAS)))


async def _enter_and_leave(pin_cm: Any) -> None:
    async with pin_cm:
        pass


async def close_gives_up_on_a_pin_whose_block_already_ended() -> bool:
    """A pin that is being invalidated is no longer registered: `close_pins` leaves it alone."""
    mod = _executors()
    pin_cm = mod.pin(ALIAS)
    executor = await pin_cm.__aenter__()
    await executor._invalidate()
    _registry().close_pins(asyncio.get_running_loop())
    drivers_closed = executor.drivers_closed
    await pin_cm.__aexit__(None, None, None)
    return drivers_closed
