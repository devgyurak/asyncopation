"""The fake driver and engine knobs that the lifecycle tests rely on."""

import asyncio

import pytest

from asyncopation.drivers.base import PoolStats
from tests.support.aio import wait_until
from tests.support.lifecycle.fakes import (
    FakeCloseError,
    FakeDriver,
    FakeInjectError,
    FakeOpenError,
    FakeProbeError,
    Recorder,
    Script,
    make_engine,
)


def driver(script: Script, recorder: Recorder | None = None, index: int = 0) -> FakeDriver:
    return FakeDriver(recorder or Recorder(), script, index)


async def test_a_fresh_driver_has_no_capabilities_and_zero_pool_stats():
    # given
    fake = driver(Script())
    # when
    stats = fake.pool_stats()
    # then
    assert stats == PoolStats(0, 0, 0)
    with pytest.raises(RuntimeError):
        fake.capabilities  # noqa: B018


async def test_open_marks_the_driver_open_and_counts_it():
    # given
    recorder = Recorder()
    fake = driver(Script(), recorder)
    # when
    await fake.open()
    # then
    assert (fake.is_open, recorder.opens, recorder.open_attempts) == (True, 1, 1)


async def test_fail_opens_fails_that_many_opens_then_succeeds():
    # given
    script = Script(fail_opens=1)
    recorder = Recorder()
    # when
    with pytest.raises(FakeOpenError):
        await driver(script, recorder).open()
    await driver(script, recorder).open()
    # then
    assert (recorder.open_attempts, recorder.opens) == (2, 1)


async def test_fail_open_after_fails_once_that_many_opens_succeeded():
    # given
    script = Script(fail_open_after=1)
    recorder = Recorder()
    await driver(script, recorder).open()
    # when / then
    with pytest.raises(FakeOpenError):
        await driver(script, recorder).open()


async def test_the_gate_holds_an_open_until_it_is_set():
    # given
    script = Script(gate=asyncio.Event())
    recorder = Recorder()
    task = asyncio.create_task(driver(script, recorder).open())
    await wait_until(lambda: _attempted(recorder))
    # when
    assert script.gate is not None
    still_waiting = not task.done()
    script.gate.set()
    await task
    # then
    assert still_waiting is True
    assert recorder.opens == 1


async def test_gate_after_lets_the_first_opens_through():
    # given
    script = Script(gate=asyncio.Event(), gate_after=1)
    recorder = Recorder()
    # when
    await driver(script, recorder).open()
    # then
    assert recorder.opens == 1


async def test_gate_driver_index_holds_only_that_driver():
    # given
    script = Script(gate=asyncio.Event(), gate_driver_index=1)
    recorder = Recorder()
    # when
    await driver(script, recorder, index=0).open()
    # then
    assert recorder.opens == 1


async def test_close_counts_and_closes():
    # given
    recorder = Recorder()
    fake = driver(Script(), recorder)
    await fake.open()
    # when
    await fake.close()
    # then
    assert (fake.is_open, recorder.closes) == (False, 1)


async def test_fail_closes_fails_that_many_closes_but_still_closes_the_driver():
    # given
    script = Script(fail_closes=1)
    fake = driver(script)
    await fake.open()
    # when
    with pytest.raises(FakeCloseError):
        await fake.close()
    # then
    assert fake.is_open is False


async def test_fail_close_fails_every_close():
    # given
    fake = driver(Script(fail_close=True))
    # when / then
    with pytest.raises(FakeCloseError):
        await fake.close()


async def test_the_close_gate_holds_a_close_until_it_is_set():
    # given
    script = Script(close_gate=asyncio.Event())
    recorder = Recorder()
    fake = driver(script, recorder)
    await fake.open()
    task = asyncio.create_task(fake.close())
    await wait_until(lambda: _closed_once(recorder))
    # when
    assert script.close_gate is not None
    still_open = fake.is_open
    script.close_gate.set()
    await task
    # then
    assert still_open is True
    assert fake.is_open is False


async def test_the_engine_probe_reads_capabilities_and_counts():
    # given
    recorder = Recorder()
    script = Script(version=(16, 4))
    fake = driver(script, recorder)
    await fake.open()
    engine = make_engine(recorder, script)
    # when
    info = engine.probe(fake)
    # then
    assert (info.version, recorder.probes) == ((16, 4), 1)


async def test_fail_probes_fails_the_probe():
    # given
    recorder = Recorder()
    script = Script(fail_probes=1)
    fake = driver(script, recorder)
    await fake.open()
    # when / then
    with pytest.raises(FakeProbeError):
        make_engine(recorder, script).probe(fake)


def test_fail_injects_fails_the_injection_and_records_nothing():
    # given
    recorder = Recorder()
    engine = make_engine(recorder, Script(fail_injects=1))
    # when
    with pytest.raises(FakeInjectError):
        engine.inject("default", {}, object())
    # then
    assert recorder.injects == []


async def _attempted(recorder: Recorder) -> bool:
    return recorder.open_attempts >= 1


async def _closed_once(recorder: Recorder) -> bool:
    return recorder.closes >= 1
