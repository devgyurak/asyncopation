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
import weakref
from collections.abc import Iterable
from typing import Any

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

    Idempotent. Callable again until a driver is open or opening (``RuntimeError`` afterwards),
    e.g. after a test runner renamed ``NAME`` (tests call ``asyncopation.configure()`` once the
    test databases exist); an alias whose configuration changed loses its server info. Never inside
    the loop: resolving a time zone name reads the tz database from disk.

    One alias never breaks ``ready()``: an unsupported ENGINE, a bad setting (``ImproperlyConfigured``
    from the driver's settings) or a missing driver library is recorded for that alias and raised
    by its first open.
    """
    if not registry.is_idle():
        raise RuntimeError(
            "configure() cannot run while a driver is open or opening; close() first"
        )
    use_tz, time_zone = bool(settings.USE_TZ), str(settings.TIME_ZONE)
    new: dict[str, AliasConfig] = {}
    for alias, settings_dict in configured_databases().items():
        new[alias] = _prepare(alias, settings_dict, use_tz, time_zone)
    registry.set_configs(new)


def _prepare(
    alias: str, settings_dict: dict[str, Any], use_tz: bool, time_zone: str
) -> AliasConfig:
    name = settings_dict["ENGINE"]
    factory = engines.FACTORIES.get(name)
    if factory is None:
        return AliasConfig(name, settings_dict, use_tz, time_zone)
    try:
        engine = factory()
        driver_factory = engine.prepare(alias, settings_dict, use_tz=use_tz, time_zone=time_zone)
    except Exception as exc:  # whatever is wrong with this alias must not break the others
        logger.warning("asyncopation: database %r cannot be prepared: %s", alias, exc)
        return AliasConfig(name, settings_dict, use_tz, time_zone, error=exc)
    return AliasConfig(name, settings_dict, use_tz, time_zone, engine, driver_factory)


async def ensure_driver(alias: str, *, lazy: bool = True) -> AsyncDriver:
    """The open driver of ``alias`` in the running loop, opening it (once) when needed."""
    loop = asyncio.get_running_loop()
    entry = registry.entry(alias, loop)
    if entry is not None and entry.driver is not None and not entry.closing:
        return entry.driver
    registry.purge_closed_loops()
    if registry.is_closing(loop):
        raise _closing_error(alias)
    _config(alias)  # fail early for an unknown or unsupported alias
    entry = registry.entry_or_create(alias, loop)
    async with entry.lock:
        if entry.closing:
            raise _closing_error(alias)
        if entry.driver is not None:
            return entry.driver
        config = _config(alias)  # re-read: configure() may have replaced it while we queued
        _check_unchanged(alias, config)
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
        await driver.open()  # cleans up after itself when it fails or is cancelled (ADR-0003)
        try:
            if entry.closing:  # a close() began while we were opening: it must not get this pool
                raise _closing_error(alias)
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
        task = asyncio.current_task()
        entry.opener = None if task is None else weakref.ref(task)
        return driver


def _closing_error(alias: str) -> RuntimeError:
    return RuntimeError(f"asyncopation is closing in this event loop; cannot open {alias!r}")


def _check_unchanged(alias: str, config: AliasConfig) -> None:
    """Fail closed when ``DATABASES`` changed after ``configure()`` (e.g. a test runner's rename).

    Opening with the stale configuration would reach the original database instead of the test
    database.
    """
    if (
        configured_databases().get(alias) != config.settings_dict
        or bool(settings.USE_TZ) != config.use_tz
        or str(settings.TIME_ZONE) != config.time_zone
    ):
        raise ImproperlyConfigured(
            "DATABASES changed since configure(); call asyncopation.configure() after the test "
            "databases are created"
        )


def _config(alias: str) -> AliasConfig:
    configs = registry.configs()
    if not configs:
        raise ImproperlyConfigured(
            "asyncopation is not configured: add 'asyncopation' to INSTALLED_APPS "
            "(or call asyncopation.configure())"
        )
    config = configs.get(alias)
    if config is None:
        raise ImproperlyConfigured(f"asyncopation: unknown database alias {alias!r}")
    if config.error is not None:
        raise ImproperlyConfigured(
            f"asyncopation: database {alias!r} cannot be used: {config.error}"
        ) from config.error
    if config.engine is None:
        raise ImproperlyConfigured(
            f"asyncopation: ENGINE {config.engine_name!r} of {alias!r} is not supported yet "
            "(MySQL/MariaDB arrive with issue #7)"
        )
    return config


async def setup(aliases: Iterable[str] | None = None) -> None:
    """Open the drivers of ``aliases`` in the running loop.

    Without ``aliases`` every configured alias that has a supported engine is opened and the others
    are skipped with an INFO log (a MySQL-only alias does not stop a PostgreSQL one). Aliases named
    explicitly must be usable: an unsupported or misconfigured one raises. A single ``str`` is a
    ``TypeError`` (it would be read as a sequence of characters).

    All or nothing: when an alias fails (or the call is cancelled), the drivers this call opened are
    closed again before raising; drivers that were already open stay open.
    """
    if isinstance(aliases, str):
        raise TypeError("setup(aliases) takes an iterable of alias names, not a single str")
    names = _supported_aliases() if aliases is None else list(aliases)
    loop = asyncio.get_running_loop()
    opened: list[registry.Entry] = []
    try:
        for alias in names:
            before = registry.entry(alias, loop)
            was_open = before is not None and before.driver is not None
            await ensure_driver(alias, lazy=False)
            entry = registry.entry_or_create(alias, loop)
            # only a driver this very call opened: a concurrent task may have opened it meanwhile
            if (
                not was_open
                and entry.opener is not None
                and entry.opener() is asyncio.current_task()
            ):
                opened.append(entry)
    except BaseException:
        for entry in opened:
            entry.closing = (
                True  # a concurrent opener now fails instead of opening on a doomed entry
            )
        try:
            with contextlib.suppress(Exception):
                await asyncio.shield(_close_entries(opened))
        finally:
            for entry in opened:
                registry.forget(entry)
        raise


def _supported_aliases() -> list[str]:
    usable: list[str] = []
    for alias, config in registry.configs().items():
        if config.engine is not None and config.error is None:
            usable.append(alias)
        else:
            logger.info("asyncopation.setup(): skipping %r (%s)", alias, _skip_reason(config))
    return usable


def _skip_reason(config: AliasConfig) -> str:
    if config.error is not None:
        return f"not usable: {config.error}"
    return f"ENGINE {config.engine_name!r} is not supported yet"


async def close(timeout: float = DEFAULT_CLOSE_TIMEOUT) -> None:  # noqa: ASYNC109 - public API, applied via asyncio.timeout
    """Close the running loop's drivers and drop their entries; server info is kept.

    New opens are refused first, then in-flight statements get ``timeout`` seconds to finish.
    The drivers are closed either way; ``TimeoutError`` is raised afterwards when statements were
    still running. Pools of other loops are not touched. An open in progress is awaited first (its
    driver is then closed, and its caller gets a ``RuntimeError``), so ``close()`` can outlast
    ``timeout`` by the pool's connect time. Overlapping ``close()`` calls are safe: each one
    removes only the entries it began with.
    """
    loop = asyncio.get_running_loop()
    mine = registry.begin_closing(loop)
    timed_out = False
    failures: list[Exception] = []
    interrupted = False
    try:
        try:
            async with asyncio.timeout(timeout):
                await registry.wait_idle(loop)
        except TimeoutError:
            timed_out = True
    except BaseException:
        interrupted = True  # close() itself is being cancelled: nobody would see the failures
        raise
    finally:
        failures = await asyncio.shield(_close_and_end(loop, mine))
        if interrupted:
            for failure in failures:
                logger.error("asyncopation: a driver failed to close: %r", failure)
    for extra in failures[1:]:
        logger.error("asyncopation: another driver failed to close: %r", extra)
    if failures:
        raise failures[0]
    if timed_out:
        raise TimeoutError(f"statements still in flight after {timeout}s; drivers were closed")


async def _close_and_end(
    loop: asyncio.AbstractEventLoop, mine: list[registry.Entry]
) -> list[Exception]:
    try:
        return await _close_entries(mine)
    finally:
        registry.end_closing(loop, mine)


async def _close_entries(entries: list[registry.Entry]) -> list[Exception]:
    """Close each entry's driver (after any open in progress); one failure does not stop the rest."""
    failures: list[Exception] = []
    for entry in entries:
        async with entry.lock:
            driver, entry.driver = entry.driver, None
            if driver is not None:
                try:
                    await driver.close()
                except Exception as exc:
                    failures.append(exc)
    return failures
