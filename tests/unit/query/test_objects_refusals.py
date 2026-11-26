"""What `Model.aobjects` refuses (ADR-0009 Decisions 3 and 4): the blocking tables come before the
manager, and no coroutine, async generator or other awaitable of the manager is run or handed out
(each would end in Django's `sync_to_async`, a thread hop, or leak pending async work)."""

import pytest

from asyncopation import SyncEvaluationError
from tests.support.aq_wrapper import call_named, read_attribute, sync_evaluator_names
from tests.support.manager_models import (
    AsyncItem,
    BlockedItem,
    attach_to_member,
    cancelled_after_a_turn,
    detach_from_member,
    discard_handed_out,
    handed_out_future,
    is_open,
    refusal_of,
    remove_pending_coroutine,
    store_pending_coroutine,
)
from tests.support.object_models import Plain

DEFINED = "is a coroutine or an async generator"
RETURNED = "returned an awaitable or an async generator"


@pytest.fixture
def attached_member():
    """`AsyncObjects.attach(Member)` (a model with Django's `BaseUserManager`), undone afterwards."""
    model = attach_to_member()
    yield model
    detach_from_member()


@pytest.fixture
def pending_coroutine():
    """A not-yet-started coroutine object stored as `AsyncItem`'s manager attribute `pending`;
    closed and removed afterwards."""
    coroutine = store_pending_coroutine(AsyncItem, "pending")
    yield coroutine
    remove_pending_coroutine(AsyncItem, "pending", coroutine)


@pytest.fixture
async def handed_out():
    """Cancels, awaits and forgets the future a manager method handed out."""
    yield
    await discard_handed_out()


# -- the blocking tables -------------------------------------------------------------------------


@pytest.mark.parametrize("name", sync_evaluator_names())
def test_a_sync_evaluator_raises_sync_evaluation_error(no_sql, no_thread_hop, name):
    # given
    bound = Plain.aobjects
    # when / then
    with pytest.raises(SyncEvaluationError):
        call_named(bound, name)


@pytest.mark.parametrize("name", ["acreate", "aiterator", "aaggregate", "alast", "raw"])
def test_an_unimplemented_django_coroutine_or_raw_raises_not_implemented(
    no_sql, no_thread_hop, name
):
    # given
    bound = Plain.aobjects
    # when / then
    with pytest.raises(NotImplementedError):
        call_named(bound, name)


@pytest.mark.parametrize("name", ["get", "count"])
def test_a_sync_evaluator_the_manager_overrides_still_raises_sync_evaluation_error(
    no_sql, no_thread_hop, name
):
    # given
    bound = BlockedItem.aobjects
    # when / then
    with pytest.raises(SyncEvaluationError):
        call_named(bound, name)


@pytest.mark.parametrize("name", ["acreate", "aiterator", "raw"])
def test_a_blocked_name_the_manager_overrides_still_raises_not_implemented(
    no_sql, no_thread_hop, name
):
    # given
    bound = BlockedItem.aobjects
    # when / then
    with pytest.raises(NotImplementedError):
        call_named(bound, name)


# -- coroutines and async generators the manager defines -----------------------------------------


@pytest.mark.parametrize(
    "name",
    [
        pytest.param("anative", id="async-def"),
        pytest.param("astream", id="async-generator-function"),
        pytest.param("asynced", id="sync-to-async-attribute"),
    ],
)
def test_an_async_attribute_of_the_manager_is_refused_before_the_call(
    no_sql, no_thread_hop, no_sync_to_async, name
):
    # given
    bound = AsyncItem.aobjects
    # when
    refusal = refusal_of(lambda: call_named(bound, name), name)
    # then
    assert refusal.message is not None
    assert DEFINED in refusal.message
    assert "not supported" in refusal.message


@pytest.mark.parametrize("name", ["anative", "astream", "asynced"])
def test_an_async_attribute_of_the_manager_is_never_called(
    no_sql, no_thread_hop, no_sync_to_async, name
):
    # given
    bound = AsyncItem.aobjects
    # when
    refusal = refusal_of(lambda: call_named(bound, name), name)
    # then
    assert refusal.unawaited == []
    assert refusal.thread_starts == 0


def test_a_django_coroutine_of_the_manager_is_refused(
    attached_member, no_sql, no_thread_hop, no_sync_to_async
):
    # when
    refusal = refusal_of(
        lambda: attached_member.aobjects.aget_by_natural_key("someone"), "aget_by_natural_key"
    )
    # then
    assert refusal.message is not None
    assert DEFINED in refusal.message
    assert refusal.unawaited == []
    assert refusal.thread_starts == 0


# -- async work a manager hands out --------------------------------------------------------------


@pytest.mark.parametrize(
    ("name", "coroutine"),
    [
        pytest.param("acount_active", "acount", id="returns-a-coroutine"),
        pytest.param("stream", "aiterator", id="returns-an-async-generator"),
    ],
)
def test_a_sync_manager_method_handing_out_async_work_is_refused(
    no_sql, no_thread_hop, no_sync_to_async, name, coroutine
):
    # given
    bound = AsyncItem.aobjects
    # when
    refusal = refusal_of(lambda: call_named(bound, name), coroutine)
    # then
    assert refusal.message is not None
    assert RETURNED in refusal.message
    assert "not supported" in refusal.message
    assert refusal.thread_starts == 0


def test_a_coroutine_returned_by_a_sync_manager_method_is_closed(
    no_sql, no_thread_hop, no_sync_to_async
):
    # given
    bound = AsyncItem.aobjects
    # when
    refusal = refusal_of(lambda: bound.acount_active(), "acount")
    # then
    assert refusal.message is not None
    assert refusal.unawaited == []


def test_a_coroutine_stored_as_a_manager_attribute_is_refused(
    pending_coroutine, no_sql, no_thread_hop, no_sync_to_async
):
    # given
    bound = AsyncItem.aobjects
    # when
    refusal = refusal_of(lambda: read_attribute(bound, "pending"), "_pending")
    # then
    assert refusal.message is not None
    assert "is an awaitable or an async generator" in refusal.message
    assert refusal.thread_starts == 0


def test_a_coroutine_stored_as_a_manager_attribute_is_not_closed(
    pending_coroutine, no_sql, no_thread_hop, no_sync_to_async
):
    # given
    bound = AsyncItem.aobjects
    # when
    refusal_of(lambda: read_attribute(bound, "pending"), "_pending")
    # then
    assert is_open(pending_coroutine) is True


@pytest.mark.parametrize("name", ["future", "task"])
async def test_a_future_returned_by_a_sync_manager_method_is_refused(
    handed_out, no_sql, no_thread_hop, no_sync_to_async, name
):
    # given
    bound = AsyncItem.aobjects
    # when
    refusal = refusal_of(lambda: call_named(bound, name), name)
    # then
    assert refusal.message is not None
    assert RETURNED in refusal.message


@pytest.mark.parametrize("name", ["future", "task"])
async def test_a_future_returned_by_a_sync_manager_method_is_cancelled(
    handed_out, no_sql, no_thread_hop, no_sync_to_async, name
):
    # given
    bound = AsyncItem.aobjects
    # when
    refusal_of(lambda: call_named(bound, name), name)
    # then
    assert await cancelled_after_a_turn(handed_out_future()) is True
