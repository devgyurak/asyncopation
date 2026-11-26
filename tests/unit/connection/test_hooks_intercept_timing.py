"""Event timings of intercepted statements (issue #4, ADR-0005) on a fake clock: observers wrap the
interceptor chain, so `query_time` includes the interceptors' time, while `queue_time` stays the
wait for a pooled connection."""

import pytest

from tests.support.hooks.clock import QUERY, QUEUE, install_fake_clock
from tests.support.hooks.intercepting import INTERCEPTOR, timed_intercepted

PINNED = pytest.mark.parametrize("pinned", [False, True], ids=["borrowed", "pinned"])
OPS = pytest.mark.parametrize("op", ["fetch_all", "execute", "execute_many"])


@OPS
@PINNED
async def test_query_time_includes_the_interceptor_time(execenv, monkeypatch, op, pinned):
    # given
    clock = install_fake_clock(monkeypatch)
    # when
    observed = await timed_intercepted(execenv, clock, op, pinned=pinned)
    # then
    assert observed.finished.query_time == INTERCEPTOR + QUERY


@OPS
async def test_queue_time_excludes_the_interceptor_time(execenv, monkeypatch, op):
    # given
    clock = install_fake_clock(monkeypatch)
    # when
    observed = await timed_intercepted(execenv, clock, op, pinned=False)
    # then
    assert (observed.started.queue_time, observed.finished.queue_time) == (QUEUE, QUEUE)


@OPS
async def test_total_time_of_an_intercepted_statement_is_queue_plus_query_time(
    execenv, monkeypatch, op
):
    # given
    clock = install_fake_clock(monkeypatch)
    # when
    observed = await timed_intercepted(execenv, clock, op, pinned=False)
    # then
    assert observed.finished.total_time == QUEUE + INTERCEPTOR + QUERY


@OPS
@PINNED
async def test_query_time_of_a_suppressed_statement_is_the_interceptor_time(
    execenv, monkeypatch, op, pinned
):
    # given
    clock = install_fake_clock(monkeypatch)
    # when
    observed = await timed_intercepted(execenv, clock, op, pinned=pinned, suppress=True)
    # then
    assert observed.finished.query_time == INTERCEPTOR
