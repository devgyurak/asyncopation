"""The ADR-0005 observer API (issue #4): `QueryEvent`, `observe`, `add_observer`,
`remove_observer` and their re-exports."""

import dataclasses

import pytest

import asyncopation
import asyncopation.connection as connection
from asyncopation.connection import hooks
from tests.support.hooks.events import (
    AsyncCall,
    Recorder,
    async_observer,
    field_names,
    started_event,
)
from tests.support.hooks.scenarios import statement_with_unentered_observe
from tests.support.hooks.scripts import UNCONFIGURED_SETTINGS
from tests.support.lifecycle_scripts import run_isolated


def test_query_event_fields_are_in_the_adr_order():
    # when
    names = field_names(hooks.QueryEvent)
    # then
    assert names == [
        "name",
        "alias",
        "kind",
        "sql",
        "params",
        "many",
        "rowcount",
        "error",
        "queue_time",
        "query_time",
        "total_time",
    ]


def test_query_event_is_frozen():
    # given
    event = started_event()
    # when / then
    with pytest.raises(dataclasses.FrozenInstanceError):
        event.sql = "SELECT 2"


def test_query_event_is_slotted():
    # when
    event = started_event()
    # then
    assert hasattr(event, "__dict__") is False


def test_observe_returns_a_synchronous_context_manager():
    # given
    recorder = Recorder()
    # when
    manager = hooks.observe(recorder)
    # then
    assert (hasattr(manager, "__enter__"), hasattr(manager, "__exit__")) == (True, True)


def test_observe_entered_again_after_exit_raises_runtime_error():
    # given
    manager = hooks.observe(Recorder())
    with manager:
        pass
    # when / then
    with pytest.raises(RuntimeError), manager:
        pass


def test_observe_entered_again_while_active_raises_runtime_error():
    # given
    manager = hooks.observe(Recorder())
    # when / then
    with manager, pytest.raises(RuntimeError), manager:
        pass


async def test_observe_that_is_never_entered_registers_nothing(execenv):
    # when
    recorder = await statement_with_unentered_observe()
    # then
    assert recorder.events == []


REGISTRARS = pytest.mark.parametrize("register", ["observe", "add_observer"])


@REGISTRARS
@pytest.mark.parametrize(
    "fn",
    [
        pytest.param(async_observer, id="coroutine-function"),
        pytest.param(AsyncCall(), id="instance-with-async-call"),
    ],
)
def test_registering_an_asynchronous_observer_raises_type_error(register, fn):
    # given
    registrar = getattr(hooks, register)
    # when / then
    with pytest.raises(TypeError, match="synchronous"):
        registrar(fn)


@REGISTRARS
@pytest.mark.parametrize(
    "fn",
    [pytest.param(None, id="none"), pytest.param("print", id="string"), pytest.param(42, id="int")],
)
def test_registering_a_non_callable_raises_type_error(register, fn):
    # given
    registrar = getattr(hooks, register)
    # when / then
    with pytest.raises(TypeError, match="must be callable"):
        registrar(fn)


def test_remove_observer_of_an_unregistered_callable_raises_value_error():
    # when / then
    with pytest.raises(ValueError, match="registered"):
        hooks.remove_observer(Recorder())


def test_remove_observer_a_second_time_raises_value_error():
    # given
    recorder = Recorder()
    hooks.add_observer(recorder)
    hooks.remove_observer(recorder)
    # when / then
    with pytest.raises(ValueError, match="registered"):
        hooks.remove_observer(recorder)


@pytest.mark.parametrize("name", ["observe", "add_observer", "remove_observer", "QueryEvent"])
def test_connection_package_reexports_the_hooks_api(name):
    # when
    resolved = getattr(connection, name)
    # then
    assert resolved is getattr(hooks, name)


@pytest.mark.parametrize("name", ["observe", "add_observer", "remove_observer", "QueryEvent"])
def test_connection_package_lists_the_hooks_api_in_all(name):
    # when
    exported = connection.__all__
    # then
    assert name in exported


def test_top_level_observe_is_the_hooks_observe():
    # when
    resolved = asyncopation.observe
    # then
    assert resolved is hooks.observe


@pytest.mark.parametrize("name", ["add_observer", "remove_observer"])
def test_global_registration_is_not_a_top_level_export(name):
    # when / then
    with pytest.raises(AttributeError):
        getattr(asyncopation, name)


@pytest.mark.parametrize("name", ["add_observer", "remove_observer"])
def test_global_registration_is_not_listed_in_the_top_level_all(name):
    # when
    exported = asyncopation.__all__
    # then
    assert name not in exported


def test_top_level_all_lists_observe():
    # when
    exported = asyncopation.__all__
    # then
    assert "observe" in exported


def test_an_observed_call_without_django_settings_hides_the_params():
    # when
    ran = run_isolated(UNCONFIGURED_SETTINGS)
    # then
    assert (ran.returncode, ran.stderr, ran.stdout) == (0, "", "[None, None] [(1,)] False")
