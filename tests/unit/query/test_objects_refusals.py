"""What `Model.aobjects` refuses (ADR-0009 Decisions 3 and 4): the blocking tables come before the
manager, and no coroutine or async generator of the manager is run or handed out (each would end in
Django's `sync_to_async`, a thread hop)."""

import pytest

from asyncopation import SyncEvaluationError
from tests.support.aq_wrapper import sync_evaluator_names
from tests.support.manager_models import (
    AsyncItem,
    BlockedItem,
    attach_to_member,
    call_named,
    detach_from_member,
    refusal_of,
)
from tests.support.object_models import Plain


@pytest.fixture
def attached_member():
    """`AsyncObjects.attach(Member)` (a model with Django's `BaseUserManager`), undone afterwards."""
    model = attach_to_member()
    yield model
    detach_from_member()


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
def test_an_async_attribute_of_the_manager_is_refused(
    no_sql, no_thread_hop, no_sync_to_async, name
):
    # given
    bound = AsyncItem.aobjects
    # when
    refusal = refusal_of(lambda: call_named(bound, name))
    # then
    assert refusal.message is not None
    assert "not supported" in refusal.message


@pytest.mark.parametrize("name", ["anative", "astream", "asynced"])
def test_an_async_attribute_of_the_manager_is_never_called(
    no_sql, no_thread_hop, no_sync_to_async, name
):
    # given
    bound = AsyncItem.aobjects
    # when
    refusal = refusal_of(lambda: call_named(bound, name))
    # then
    assert refusal.runtime_warnings == []
    assert refusal.threads_started == 0


def test_a_django_coroutine_of_the_manager_is_refused(
    attached_member, no_sql, no_thread_hop, no_sync_to_async
):
    # when
    refusal = refusal_of(lambda: attached_member.aobjects.aget_by_natural_key("someone"))
    # then
    assert refusal.message is not None
    assert "not supported" in refusal.message
    assert refusal.runtime_warnings == []
    assert refusal.threads_started == 0


# -- sync manager methods that hand out a coroutine or an async generator ------------------------


@pytest.mark.parametrize(
    "name",
    [
        pytest.param("acount_active", id="returns-a-coroutine"),
        pytest.param("stream", id="returns-an-async-generator"),
    ],
)
def test_a_sync_manager_method_handing_out_async_work_is_refused(
    no_sql, no_thread_hop, no_sync_to_async, name
):
    # given
    bound = AsyncItem.aobjects
    # when
    refusal = refusal_of(lambda: call_named(bound, name))
    # then
    assert refusal.message is not None
    assert "not supported" in refusal.message
    assert refusal.threads_started == 0


@pytest.mark.filterwarnings("error::RuntimeWarning")
def test_a_coroutine_returned_by_a_sync_manager_method_is_closed(
    no_sql, no_thread_hop, no_sync_to_async
):
    # given
    bound = AsyncItem.aobjects
    # when
    refusal = refusal_of(lambda: bound.acount_active())
    # then
    assert refusal.message is not None
    assert refusal.runtime_warnings == []
