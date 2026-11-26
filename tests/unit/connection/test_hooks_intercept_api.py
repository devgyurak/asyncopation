"""The ADR-0005 interceptor API (issue #4): `intercept`, `add_interceptor`, `remove_interceptor`,
`QueueObserver` and their re-exports."""

import pytest

import asyncopation
import asyncopation.connection as connection
from asyncopation.connection import hooks
from tests.support.hooks.interceptors import PassThrough, Registrable, interceptor_registered
from tests.support.hooks.scenarios import registered


def test_intercept_returns_a_synchronous_context_manager():
    # when
    manager = hooks.intercept(PassThrough())
    # then
    assert (hasattr(manager, "__enter__"), hasattr(manager, "__exit__")) == (True, True)


def test_intercept_entered_again_after_exit_raises_runtime_error():
    # given
    manager = hooks.intercept(PassThrough())
    with manager:
        pass
    # when / then
    with pytest.raises(RuntimeError), manager:
        pass


def test_intercept_entered_again_while_active_raises_runtime_error():
    # given
    manager = hooks.intercept(PassThrough())
    # when / then
    with manager, pytest.raises(RuntimeError), manager:
        pass


@pytest.mark.parametrize("register", ["intercept", "add_interceptor"])
@pytest.mark.parametrize(
    "fn",
    [pytest.param(None, id="none"), pytest.param("print", id="string"), pytest.param(42, id="int")],
)
def test_registering_a_non_callable_interceptor_raises_type_error(register, fn):
    # given
    registrar = getattr(hooks, register)
    # when / then
    with pytest.raises(TypeError, match="must be callable"):
        registrar(fn)


def test_remove_interceptor_of_an_unregistered_callable_raises_value_error():
    # when / then
    with pytest.raises(ValueError, match="registered"):
        hooks.remove_interceptor(PassThrough())


def test_remove_interceptor_a_second_time_raises_value_error():
    # given
    interceptor = PassThrough()
    hooks.add_interceptor(interceptor)
    hooks.remove_interceptor(interceptor)
    # when / then
    with pytest.raises(ValueError, match="registered"):
        hooks.remove_interceptor(interceptor)


def test_remove_observer_does_not_remove_an_interceptor():
    # given
    fn = Registrable()
    # when / then
    with interceptor_registered(fn), pytest.raises(ValueError, match="observer"):
        hooks.remove_observer(fn)


def test_remove_interceptor_does_not_remove_an_observer():
    # given
    fn = Registrable()
    # when / then
    with registered(fn), pytest.raises(ValueError, match="interceptor"):
        hooks.remove_interceptor(fn)


def test_remove_interceptor_leaves_an_observer_of_the_same_callable_registered():
    # given
    fn = Registrable()
    hooks.add_observer(fn)
    hooks.add_interceptor(fn)
    # when
    hooks.remove_interceptor(fn)
    # then
    hooks.remove_observer(fn)  # still registered: no ValueError
    assert hooks._global == ()


@pytest.mark.parametrize("name", ["intercept", "add_interceptor", "remove_interceptor"])
def test_connection_package_reexports_the_interceptor_api(name):
    # when
    resolved = getattr(connection, name)
    # then
    assert resolved is getattr(hooks, name)


def test_connection_package_reexports_the_queue_observer():
    # when
    resolved = connection.QueueObserver
    # then
    assert resolved is hooks.QueueObserver


@pytest.mark.parametrize(
    "name", ["intercept", "add_interceptor", "remove_interceptor", "QueueObserver"]
)
def test_connection_package_lists_the_interceptor_api_in_all(name):
    # when
    exported = connection.__all__
    # then
    assert name in exported


def test_top_level_intercept_is_the_hooks_intercept():
    # when
    resolved = asyncopation.intercept
    # then
    assert resolved is hooks.intercept


def test_top_level_all_lists_intercept():
    # when
    exported = asyncopation.__all__
    # then
    assert "intercept" in exported


@pytest.mark.parametrize("name", ["add_interceptor", "remove_interceptor", "QueueObserver"])
def test_global_interceptor_registration_and_queue_observer_are_not_top_level_exports(name):
    # when / then
    with pytest.raises(AttributeError):
        getattr(asyncopation, name)


@pytest.mark.parametrize("name", ["add_interceptor", "remove_interceptor", "QueueObserver"])
def test_global_interceptor_registration_and_queue_observer_are_not_in_the_top_level_all(name):
    # when
    exported = asyncopation.__all__
    # then
    assert name not in exported


def test_the_interceptable_calls_are_the_three_statements():
    # when
    interceptable = hooks.INTERCEPTABLE
    # then
    assert interceptable == frozenset({"fetch_all", "execute", "execute_many"})
