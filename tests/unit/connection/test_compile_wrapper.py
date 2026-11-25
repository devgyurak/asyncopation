"""`compile_wrapper`: the alias must be configured first, then open (ADR-0004 Decision 2)."""

import pytest
from django.core.exceptions import ImproperlyConfigured

from asyncopation.connection.lifecycle import compile_wrapper
from tests.support.lifecycle.fakes import INJECTED_WRAPPER


def test_compile_wrapper_of_a_configured_alias_that_is_not_open_raises_runtime_error(configured):
    # when / then
    with pytest.raises(RuntimeError, match="not open"):
        compile_wrapper("default")


def test_compile_wrapper_of_an_unknown_alias_raises_improperly_configured_not_runtime_error(
    configured,
):
    # when / then
    with pytest.raises(ImproperlyConfigured, match="unknown database alias"):
        compile_wrapper("nope")


def test_compile_wrapper_of_an_open_alias_returns_what_the_engine_injected(opened):
    # when
    wrapper = compile_wrapper("default")
    # then
    assert wrapper is INJECTED_WRAPPER
