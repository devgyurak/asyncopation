"""Teardown and leak checks: nothing may stay in the registry after a test."""

from __future__ import annotations


async def dispose_environment() -> None:
    """Per-test teardown of the fake environment: asserts that no driver leaked (see below)."""
    await teardown_lifecycle()


async def teardown_lifecycle() -> None:
    """Close what the test left open on this loop, then assert nothing is left, then reset.

    A driver left open in ANOTHER loop (or one `close()` could not remove) fails the check; the
    registry is reset either way so that one leak does not cascade into the next test.
    """
    from asyncopation.connection import registry
    from asyncopation.connection.lifecycle import close

    try:
        await close(timeout=1.0)
        leaked = [*registry.entries(), *registry.take_leaked()]
        assert leaked == [], f"drivers left open in the registry after the test: {leaked}"
    finally:
        registry.reset()


def configure_session() -> None:
    """Session start (after the test DB exists): re-run `configure()` with the test DB name."""
    from asyncopation.connection import configure, registry

    registry.reset()
    configure()


def assert_registry_empty_at_session_end() -> None:
    from asyncopation.connection import registry

    leaked = registry.entries()
    registry.reset()
    assert leaked == [], f"drivers left open in the registry at session end: {leaked}"


def drain_in_flight(alias: str) -> None:
    """Finish every statement a test started (so that teardown can close without waiting)."""
    from asyncopation.connection import registry

    while registry.in_flight(alias) > 0:
        registry.statement_finished(alias)


def assert_registry_clean() -> None:
    """Fail when a test left a driver in the registry (or one was dropped with a closed loop)."""
    from asyncopation.connection import registry

    leftovers = [*registry.entries(), *registry.take_leaked()]
    registry.reset()
    assert leftovers == [], f"the test left drivers in the registry: {leftovers}"
