"""ADR-0005 observers on a real PostgreSQL connection (issue #4): events of a real statement and
stream, and the SQL of a live `alist()`."""

from functools import partial

import pytest

from harness.catalog.models import Book
from harness.settings import VENDOR
from tests.support.hooks.alist import observed_live_alist
from tests.support.hooks.events import BOTH
from tests.support.hooks.postgres import (
    observed_real_fetch_all,
    observed_real_stream,
    observed_real_transaction,
    real_stream_with_consumer_error,
)
from tests.support.hooks.streams import ConsumerError

pytestmark = pytest.mark.skipif(
    VENDOR != "postgresql",
    reason="the executors run on asyncmy with issue #7b; the observer integration tests run on "
    "PostgreSQL only until then",
)

PINNED = pytest.mark.parametrize("pinned", [False, True], ids=["borrowed", "pinned"])


async def test_a_real_fetch_all_is_observed_once_as_started_then_finished(spy):
    # when
    observed = await observed_real_fetch_all()
    # then
    assert observed.names == BOTH


async def test_a_real_fetch_all_finishes_with_the_number_of_rows(spy):
    # when
    observed = await observed_real_fetch_all()
    # then
    assert observed.finished.rowcount == len(observed.result) == 3


async def test_a_real_fetch_all_has_a_positive_query_time(spy):
    # when
    observed = await observed_real_fetch_all()
    # then
    assert observed.finished.query_time > 0


async def test_a_real_fetch_all_total_time_is_queue_plus_query_time(spy):
    # when
    observed = await observed_real_fetch_all()
    # then
    finished = observed.finished
    assert finished.total_time == finished.queue_time + finished.query_time


@PINNED
async def test_a_real_stream_finishes_with_the_total_number_of_rows_read(spy, pinned):
    # when
    observed = await observed_real_stream(pinned=pinned)
    # then
    assert observed.names == BOTH
    assert observed.finished.rowcount == 25


async def test_real_transaction_control_is_observed_with_its_kind_and_descriptive_sql(spy):
    # when
    observed = await observed_real_transaction()
    # then
    assert observed.recorder.kinds_and_sqls == [
        ("transaction", "BEGIN"),
        ("query", "SAVEPOINT sp"),
        ("query", "RELEASE SAVEPOINT sp"),
        ("transaction", "COMMIT"),
    ]
    assert observed.error is None


@PINNED
async def test_a_consumer_error_in_a_real_stream_is_not_the_stream_error(spy, pinned):
    # when
    observed = await real_stream_with_consumer_error(pinned=pinned)
    # then
    assert isinstance(observed.error, ConsumerError)
    assert observed.finished.error is None


@pytest.mark.parametrize(
    "build",
    [
        pytest.param(Book.objects.all, id="all"),
        pytest.param(partial(Book.objects.filter, published=True), id="filter"),
        pytest.param(lambda: Book.objects.select_related("author"), id="select-related"),
        pytest.param(lambda: Book.objects.order_by("pk")[2:5], id="slice"),
    ],
)
async def test_a_live_alist_is_observed_with_the_sql_django_compiles(async_env, build):
    # when
    live = await observed_live_alist(build)
    # then
    assert live.rows > 0
    assert live.observed == live.compiled
