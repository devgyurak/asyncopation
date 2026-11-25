"""Configure, open, probe and close (ADR-0004).

``configure()`` runs once at ``AppConfig.ready()`` (no I/O, no asyncio objects) and may be called
again while no driver is open. ``ensure_driver`` is the single open path - ``setup()`` and the
lazy fallback both use it: build the driver, ``open()`` it, probe, inject, in that order, under the
``(alias, loop)`` lock.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import Iterable

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured

from asyncopation._django.connections import configured_databases
from asyncopation.connection import engines, registry
from asyncopation.connection.registry import AliasConfig
from asyncopation.drivers.base import AsyncDriver

logger = logging.getLogger("asyncopation.connection")

DEFAULT_CLOSE_TIMEOUT = 10.0

__all__ = ["close", "configure", "ensure_driver", "setup"]


def configure() -> None:
    """Build the per-alias configuration from Django's configured ``DATABASES``.

    Idempotent. Callable again until a driver is open (``RuntimeError`` afterwards), e.g. after a
    test runner renamed ``NAME``; an alias whose configuration changed loses its server info. An
    unsupported ENGINE is recorded and fails at the first open, not here. Never inside the loop:
    resolving a time zone name reads the tz database from disk.
    """
    if not registry.is_idle():
        raise RuntimeError(
            "configure() cannot run while a driver is open or opening; close() first"
        )
    use_tz, time_zone = bool(settings.USE_TZ), str(settings.TIME_ZONE)
    new: dict[str, AliasConfig] = {}
    for alias, settings_dict in configured_databases().items():
        name = settings_dict["ENGINE"]
        factory = engines.FACTORIES.get(name)
        engine = factory() if factory is not None else None
        driver_factory = (
            engine.prepare(alias, settings_dict, use_tz=use_tz, time_zone=time_zone)
            if engine is not None
            else None
        )
        new[alias] = AliasConfig(name, settings_dict, use_tz, time_zone, engine, driver_factory)
    registry.set_configs(new)


async def ensure_driver(alias: str, *, lazy: bool = True) -> AsyncDriver:
    """The open driver of ``alias`` in the running loop, opening it (once) when needed."""
    loop = asyncio.get_running_loop()
    entry = registry.entry(alias, loop)
    if entry is not None and entry.driver is not None and not entry.closing:
        return entry.driver
    if registry.is_closing(loop):
        raise RuntimeError(f"asyncopation is closing in this event loop; cannot open {alias!r}")
    config = _config(alias)
    entry = registry.entry_or_create(alias, loop)
    async with entry.lock:
        if entry.closing:
            raise RuntimeError(f"asyncopation is closing in this event loop; cannot open {alias!r}")
        if entry.driver is not None:
            return entry.driver
        config = _config(alias)  # re-read: configure() may have replaced it while we queued
        if lazy and not entry.warned:
            entry.warned = True
            logger.warning(
                "asyncopation opened %r lazily on first use; await asyncopation.setup() at "
                "startup (asyncopation.connection.asgi.LifespanApp does it)",
                alias,
            )
        if config.engine is None or config.factory is None:  # narrowed by _config
            raise ImproperlyConfigured(f"asyncopation: {alias!r} has no engine")
        driver: AsyncDriver = config.factory()
        await driver.open()
        try:
            info = registry.server_info(alias)  # probed once per alias per process
            if info is None:
                info = config.engine.probe(driver)
            config.engine.inject(alias, config.settings_dict, info)
            registry.publish_server_info(alias, info)  # only after a successful injection
        except BaseException:
            # a failing close must not mask the probe/injection error, nor leak the pool when
            # this task is cancelled again meanwhile
            with contextlib.suppress(Exception):
                await asyncio.shield(driver.close())
            raise
        entry.driver = driver
        return driver


def _config(alias: str) -> AliasConfig:
    configs = registry.configs()
    if not configs:
        raise ImproperlyConfigured(
            "asyncopation is not configured: add 'asyncopation' to INSTALLED_APPS "
            "(or call asyncopation.connection.configure())"
        )
    config = configs.get(alias)
    if config is None:
        raise ImproperlyConfigured(f"asyncopation: unknown database alias {alias!r}")
    if config.engine is None:
        raise ImproperlyConfigured(
            f"asyncopation: ENGINE {config.engine_name!r} of {alias!r} is not supported yet "
            "(MySQL/MariaDB arrive with issue #7)"
        )
    return config


async def setup(aliases: Iterable[str] | None = None) -> None:
    """Open the drivers of ``aliases`` (default: every configured alias) in the running loop.

    All or nothing: when one alias fails, this loop's drivers are closed again before raising.
    """
    names = list(aliases) if aliases is not None else list(registry.configs())
    try:
        for alias in names:
            await ensure_driver(alias, lazy=False)
    except BaseException:
        # all or nothing: a failed startup leaves no pool of this loop behind
        with contextlib.suppress(Exception):
            await asyncio.shield(close())
        raise


async def close(timeout: float = DEFAULT_CLOSE_TIMEOUT) -> None:  # noqa: ASYNC109 - public API, applied via asyncio.timeout
    """Close the running loop's drivers and drop their entries; server info is kept.

    New opens are refused first, then in-flight statements get ``timeout`` seconds to finish.
    The drivers are closed either way; ``TimeoutError`` is raised afterwards when statements were
    still running. Pools of other loops are not touched. An open in progress is awaited first, so
    ``close()`` can outlast ``timeout`` by the pool's connect time.
    """
    loop = asyncio.get_running_loop()
    mine = registry.begin_closing(loop)
    timed_out = False
    failures: list[Exception] = []
    try:
        try:
            async with asyncio.timeout(timeout):
                await registry.wait_idle(loop)
        except TimeoutError:
            timed_out = True
    finally:
        failures = await asyncio.shield(_close_entries(loop, mine))
    for extra in failures[1:]:
        logger.error("asyncopation: another driver failed to close: %r", extra)
    if failures:
        raise failures[0]
    if timed_out:
        raise TimeoutError(f"statements still in flight after {timeout}s; drivers were closed")


async def _close_entries(
    loop: asyncio.AbstractEventLoop, mine: list[registry.Entry]
) -> list[Exception]:
    failures: list[Exception] = []
    try:
        for entry in mine:
            async with entry.lock:  # an open in progress finishes first
                driver, entry.driver = entry.driver, None
                if driver is not None:
                    try:
                        await driver.close()
                    except Exception as exc:  # keep closing the other pools
                        failures.append(exc)
    finally:
        registry.end_closing(loop)
    return failures
