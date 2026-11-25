"""The MySQL engine from `connection.engines`, with strict fake drivers."""

from __future__ import annotations

import dataclasses
from types import SimpleNamespace
from typing import Any

from asyncopation.drivers.base import Capabilities
from tests.support.mysql_server import MYSQL_8_4, server_data

MY_ENGINE = "django.db.backends.mysql"


def my_engine() -> Any:
    from asyncopation.connection.engines import FACTORIES

    return FACTORIES[MY_ENGINE]()


class StrictDriver:
    """Exposes only what a pure probe may read; any other attribute (a query path) fails."""

    def __init__(self, *, with_server_data: bool = True, payload: Any = None) -> None:
        self._with_server_data = with_server_data
        self._payload = payload if payload is not None else server_data()
        self.touched: list[str] = []

    @property
    def capabilities(self) -> Capabilities:
        self.touched.append("capabilities")
        return Capabilities(
            server_version=(8, 4, 11), server_side_cursors=True, atomic_execute_many=False
        )

    def __getattr__(self, name: str) -> Any:
        if name == "server_data" and self._with_server_data:
            self.touched.append("server_data")
            return self._payload
        raise AttributeError(name)


def probe_values() -> dict[str, Any]:
    """The `mysql_server_data` values `probe` is given."""
    return dict(MYSQL_8_4)


def probed(driver: Any) -> Any:
    return my_engine().probe(driver)


def probed_fields(driver: Any) -> dict[str, Any]:
    return dataclasses.asdict(probed(driver))


def touched_by_probe() -> list[str]:
    """What a probe reads from a driver (everything else would raise)."""
    driver = StrictDriver()
    probed(driver)
    return sorted(driver.touched)


def other_payload() -> Any:
    return SimpleNamespace(version="8.4.11")


def prepared_factory(**extra: Any) -> Any:
    from tests.support.mysql_driver_drift import my_drift_settings

    return my_engine().prepare(
        "default", my_drift_settings(None, **extra), use_tz=True, time_zone="UTC"
    )
