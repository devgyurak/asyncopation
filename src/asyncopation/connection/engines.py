"""Per-ENGINE wiring: driver factory, server-info probe and compile-wrapper injection (ADR-0004).

``connection`` stays engine-agnostic: everything that needs psycopg (the driver, and Django's
PostgreSQL backend behind ``compile.postgresql``) is imported inside the factory of its engine, so
a MySQL-only install never imports psycopg and ``import asyncopation`` imports none of it.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from asyncopation.compile.server_info import ServerInfo
from asyncopation.drivers.base import AsyncDriver


@dataclass(frozen=True, slots=True)
class Engine:
    """What the lifecycle needs from one database engine."""

    prepare: Callable[..., Callable[[], AsyncDriver]]
    """``prepare(alias, settings_dict, *, use_tz, time_zone)``: resolve the configuration now (no
    I/O, but it may read the tz database, so never inside the loop) and return a zero-argument
    driver factory, called once per ``(alias, loop)`` when that entry opens."""

    probe: Callable[[AsyncDriver], ServerInfo]
    """Pure and synchronous: derive the alias-level server info from an *open* driver's
    ``capabilities`` (ADR-0004 Decision 2); it runs no query."""

    inject: Callable[[str, dict[str, Any], ServerInfo], None]
    """``inject(alias, settings_dict, info)``: hand the info to the compile-only wrapper."""


def _postgresql() -> Engine:
    from asyncopation._django.apps import is_installed
    from asyncopation.compile.postgresql import wrapper_for
    from asyncopation.compile.server_info import PostgreSQLServerInfo
    from asyncopation.drivers.postgresql import PostgresDriver
    from asyncopation.drivers.postgresql.settings import build_config

    def prepare(
        alias: str, settings_dict: dict[str, Any], *, use_tz: bool, time_zone: str
    ) -> Callable[[], AsyncDriver]:
        # Like Django, hstore/citext handlers exist only with django.contrib.postgres installed;
        # resolved now (ADR-0003 Decision 2), so a later INSTALLED_APPS change needs configure().
        config = build_config(
            alias,
            settings_dict,
            use_tz=use_tz,
            time_zone=time_zone,
            register_contrib_types=is_installed("django.contrib.postgres"),
        )
        return lambda: PostgresDriver(config)

    def probe(driver: AsyncDriver) -> ServerInfo:
        major, minor = driver.capabilities.server_version[:2]
        return PostgreSQLServerInfo(pg_version=major * 10000 + minor)

    def inject(alias: str, settings_dict: dict[str, Any], info: ServerInfo) -> None:
        if not isinstance(info, PostgreSQLServerInfo):
            raise TypeError(f"expected PostgreSQLServerInfo, got {type(info).__name__}")
        wrapper_for(alias, settings_dict, info)

    return Engine(prepare, probe, inject)


FACTORIES: dict[str, Callable[[], Engine]] = {
    "django.db.backends.postgresql": _postgresql,
}
"""Django ``ENGINE`` string -> lazy engine factory. MySQL/MariaDB arrive with #7; PostGIS needs a
PostGIS compile wrapper first, so its ENGINE is unsupported until then."""
