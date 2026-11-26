"""Fixtures for the engine unit tests: an alias per test and a fake engine with a real wrapper."""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest

from tests.support.compile_wrappers import clear_alias
from tests.support.engine.evaluate import ALIAS, CompileEnv, install_compile_environment
from tests.support.executors.fakes import finish_executor_test
from tests.support.lifecycle.checks import dispose_environment
from tests.support.lifecycle.fakes import install_fake_environment


@pytest.fixture
async def compile_env(monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[CompileEnv]:
    """`default` served by a gated RecordingDriver; the injected wrapper is the PostgreSQL one."""
    fake = install_fake_environment(monkeypatch)
    env = install_compile_environment(fake, monkeypatch)
    yield env
    finish_executor_test(env, leaves_in_flight=False)
    clear_alias(ALIAS)
    await dispose_environment()
