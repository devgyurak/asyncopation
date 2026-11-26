"""Fake engine, fake driver and fake DATABASES for the lifecycle unit tests (ADR-0004)."""

from __future__ import annotations

import asyncio
import copy
import ssl
import threading
from dataclasses import dataclass, field
from typing import Any, NamedTuple
from unittest import mock

from asyncopation.compile.server_info import ServerInfo
from asyncopation.drivers.base import Capabilities, PoolStats

FAKE_ENGINE = "tests.fake.engine"


UNSUPPORTED_ENGINE = "django.db.backends.sqlite3"


PG_ENGINE = "django.db.backends.postgresql"


POSTGIS_ENGINE = "django.contrib.gis.db.backends.postgis"


DEFAULT_STATS = PoolStats(in_use=1, idle=2, waiting=3)


FAKE_VERSION = (17, 2)


class FakeOpenError(Exception):
    """Raised by the fake driver's `open()` while the script says so."""


class FakeProbeError(Exception):
    """Raised by the fake engine's `probe()` while the script says so."""


class FakeCloseError(Exception):
    """Raised by the fake driver's `close()` when the script says so."""


INJECTED_WRAPPER = object()  # what the fake engine's `inject()` returns: the "compile-only wrapper"


class FakeInjectError(Exception):
    """Raised by the fake engine's `inject()` while the script says so."""


@dataclass(frozen=True, slots=True)
class FakeInfo(ServerInfo):
    version: tuple[int, ...]


@dataclass
class Script:
    """Controls the failures and numbers of the fake engine."""

    fail_opens: int = 0
    fail_probes: int = 0
    fail_close: bool = False
    fail_closes: int = 0  # the next N `close()` calls raise (others succeed)
    fail_open_after: int | None = None  # `open()` raises once this many opens succeeded
    fail_injects: int = 0
    gate: asyncio.Event | None = None  # `open()` waits for it (created in the running loop)
    gate_after: int | None = None  # the gate only holds opens once this many succeeded
    close_gate: asyncio.Event | None = None  # `close()` waits for it (after being counted)
    gate_driver_index: int | None = None  # the gate only holds the N-th driver built
    version: tuple[int, ...] = FAKE_VERSION
    stats: PoolStats = DEFAULT_STATS


class Prepared(NamedTuple):
    alias: str
    name: str
    use_tz: bool
    time_zone: str


@dataclass
class Recorder:
    """Counts what the engine and its drivers were asked to do."""

    prepares: list[Prepared] = field(default_factory=list)
    built: list[Any] = field(default_factory=list)
    open_attempts: int = 0
    opens: int = 0
    closes: int = 0
    probes: int = 0
    injects: list[tuple[str, Any]] = field(default_factory=list)

    def last_prepare(self, alias: str) -> Prepared:
        return next(p for p in reversed(self.prepares) if p.alias == alias)


class FakeDriver:
    def __init__(self, recorder: Recorder, script: Script, index: int = 0) -> None:
        self._recorder = recorder
        self._script = script
        self.index = index
        self.is_open = False

    @property
    def capabilities(self) -> Capabilities:
        if not self.is_open:
            raise RuntimeError("not open")
        return Capabilities(
            server_version=self._script.version, server_side_cursors=True, atomic_execute_many=True
        )

    async def open(self) -> None:
        self._recorder.open_attempts += 1
        held = self._script.gate_after is None or self._recorder.opens >= self._script.gate_after
        only = self._script.gate_driver_index
        held = held and (only is None or only == self.index)
        if self._script.gate is not None and held:
            await self._script.gate.wait()
        await asyncio.sleep(0)  # a real open awaits: concurrent callers interleave here
        after = self._script.fail_open_after
        if after is not None and self._recorder.opens >= after:
            raise FakeOpenError("boom: open")
        if self._script.fail_opens > 0:
            self._script.fail_opens -= 1
            raise FakeOpenError("boom: open")
        self.is_open = True
        self._recorder.opens += 1

    async def close(self) -> None:
        self._recorder.closes += 1
        if self._script.close_gate is not None:
            await self._script.close_gate.wait()
        self.is_open = False
        if self._script.fail_closes > 0:
            self._script.fail_closes -= 1
            raise FakeCloseError("boom: close")
        if self._script.fail_close:
            raise FakeCloseError("boom: close")

    def pool_stats(self) -> PoolStats:
        return self._script.stats if self.is_open else PoolStats(0, 0, 0)


def make_engine(recorder: Recorder, script: Script) -> Any:
    from asyncopation.connection.engines import Engine

    def prepare(alias: str, settings_dict: dict[str, Any], *, use_tz: bool, time_zone: str):
        recorder.prepares.append(Prepared(alias, settings_dict["NAME"], use_tz, time_zone))

        def factory() -> FakeDriver:
            driver = FakeDriver(recorder, script, len(recorder.built))
            recorder.built.append(driver)
            return driver

        return factory

    def probe(driver: Any) -> FakeInfo:
        recorder.probes += 1
        if script.fail_probes > 0:
            script.fail_probes -= 1
            raise FakeProbeError("boom: probe")
        return FakeInfo(driver.capabilities.server_version)

    def inject(alias: str, settings_dict: dict[str, Any], info: Any) -> Any:
        if script.fail_injects > 0:
            script.fail_injects -= 1
            raise FakeInjectError("boom: inject")
        recorder.injects.append((alias, info))
        return INJECTED_WRAPPER

    return Engine(prepare=prepare, probe=probe, inject=inject)


def database_entry(name: str, engine: str) -> dict[str, Any]:
    from tests.support.compile_wrappers import PG_SETTINGS

    return {**copy.deepcopy(PG_SETTINGS), "ENGINE": engine, "NAME": name}


class FakeEnv:
    """Fake DATABASES (aliases `default` and `other`) served by a fake engine factory."""

    def __init__(self, monkeypatch: Any) -> None:
        from asyncopation.connection import engines

        self._monkeypatch = monkeypatch
        self.recorder = Recorder()
        self.script = Script()
        self.names = {"default": "fake_db", "other": "fake_other_db"}
        self.engines = {"default": FAKE_ENGINE, "other": FAKE_ENGINE}
        self.options: dict[str, dict[str, Any]] = {}
        self.updates: dict[str, dict[str, Any]] = {}
        engine = make_engine(self.recorder, self.script)
        monkeypatch.setitem(engines.FACTORIES, FAKE_ENGINE, lambda: engine)
        self._apply()

    def _apply(self) -> None:
        from django.db import connections

        databases = {}
        for alias, name in self.names.items():
            entry = database_entry(name, self.engines[alias])
            entry["OPTIONS"] = dict(self.options.get(alias, {}))
            entry.update(self.updates.get(alias, {}))
            databases[alias] = entry
        self._monkeypatch.setitem(connections.__dict__, "settings", databases)

    def rename(self, alias: str, name: str) -> None:
        self.names[alias] = name
        self._apply()

    def use_unsupported_engine(self, alias: str) -> None:
        self.use_engine(alias, UNSUPPORTED_ENGINE)

    def use_engine(self, alias: str, engine: str) -> None:
        self.engines[alias] = engine
        self._apply()

    def add_alias(self, alias: str, name: str, engine: str) -> None:
        self.names[alias] = name
        self.engines[alias] = engine
        self._apply()

    def add_broken_alias(self, alias: str, error: Exception) -> None:
        """An alias whose engine factory raises `error` when `configure()` builds it."""
        from asyncopation.connection import engines

        def factory() -> Any:
            raise error

        key = f"tests.broken.{alias}"
        self._monkeypatch.setitem(engines.FACTORIES, key, factory)
        self.add_alias(alias, f"{alias}_db", key)

    def override(self, alias: str, **settings_dict: Any) -> None:
        """Set DATABASES[alias][key] = value for every given key."""
        self.updates.setdefault(alias, {}).update(settings_dict)
        self._apply()

    def set_options(self, alias: str, options: dict[str, Any]) -> None:
        self.options[alias] = options
        self._apply()


def uncopiable_options() -> dict[str, Any]:
    """OPTIONS leaves that `copy.deepcopy` rejects (an SSL context and an object with a lock)."""
    return {
        "ssl": ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT),
        "lock": threading.Lock(),
        "nested": {"hosts": ["a", "b"]},
    }


def install_fake_environment(monkeypatch: Any) -> FakeEnv:
    from asyncopation.connection import registry

    registry.reset()
    return FakeEnv(monkeypatch)


def counts(recorder: Recorder) -> dict[str, int]:
    return {
        "built": len(recorder.built),
        "opens": recorder.opens,
        "closes": recorder.closes,
        "probes": recorder.probes,
        "injects": len(recorder.injects),
    }


def fake_driver(version: tuple[int, ...]) -> Any:
    """A stand-in driver exposing only `capabilities.server_version` (what `probe` reads)."""
    from types import SimpleNamespace

    return SimpleNamespace(capabilities=SimpleNamespace(server_version=version))


def uncopiable_snapshot_facts() -> dict[str, bool]:
    """`configured_databases()` on settings whose OPTIONS cannot be deep-copied."""
    from django.db import connections

    from asyncopation._django.connections import configured_databases

    options = uncopiable_options()
    original = {"default": {**database_entry("x", FAKE_ENGINE), "OPTIONS": options}}
    with mock.patch.dict(connections.__dict__, {"settings": original}):
        snapshot = configured_databases()
    copied = snapshot["default"]["OPTIONS"]
    return {
        "leaf_is_shared": copied["lock"] is options["lock"] and copied["ssl"] is options["ssl"],
        "options_dict_is_copied": copied is not options,
        "nested_list_is_copied": copied["nested"]["hosts"] is not options["nested"]["hosts"],
    }


def apply_change(env: FakeEnv, settings: Any, kind: str) -> None:
    """Change the settings the way a test runner (or a careless caller) could after `configure()`."""
    if kind == "host":
        env.override("default", HOST="somewhere-else")
    elif kind == "options":
        env.set_options("default", {"connect_timeout": 3})
    elif kind == "engine":
        env.use_engine("default", UNSUPPORTED_ENGINE)
    elif kind == "use_tz":
        settings.USE_TZ = not settings.USE_TZ
    elif kind == "time_zone":
        settings.TIME_ZONE = "Asia/Seoul"
    elif kind == "alias_added":
        env.add_alias("late", "late_db", FAKE_ENGINE)
    else:
        raise ValueError(kind)
