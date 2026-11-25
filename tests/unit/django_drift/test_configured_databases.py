"""`_django.connections.configured_databases()`: Django's configured DATABASES, as a structure copy (dicts and lists copied, leaves shared).

Drift protection: Django's `ConnectionHandler.configure_settings` fills defaults (TIME_ZONE None,
OPTIONS {}, TEST {}, ...); the engines rely on them being present (ADR-0004 Decision 1).
"""

import pytest

from asyncopation._django.connections import configured_databases
from tests.support.configured_databases import (
    django_databases,
    mutation_leaks_into_django,
    nested_dicts_are_shared,
)
from tests.support.lifecycle.fakes import uncopiable_snapshot_facts


def test_configured_databases_equals_djangos_configured_settings():
    # when
    result = configured_databases()
    # then
    assert result == django_databases()


@pytest.mark.parametrize(
    ("key", "expected"),
    [
        pytest.param("TIME_ZONE", None, id="time-zone"),
        pytest.param("AUTOCOMMIT", True, id="autocommit"),
        pytest.param("ATOMIC_REQUESTS", False, id="atomic-requests"),
        pytest.param("CONN_HEALTH_CHECKS", False, id="health-checks"),
    ],
)
def test_configured_databases_carries_django_defaults(key, expected):
    # when
    entry = configured_databases()["default"]
    # then
    assert entry[key] == expected


@pytest.mark.parametrize("key", ["OPTIONS", "TEST"])
def test_configured_databases_carries_the_dict_valued_defaults(key):
    # when
    entry = configured_databases()["default"]
    # then
    assert isinstance(entry[key], dict)


def test_configured_databases_returns_a_copy_that_can_be_mutated_safely():
    # when
    leaked = mutation_leaks_into_django()
    # then
    assert leaked is False


def test_configured_databases_does_not_share_nested_dicts_with_django():
    # when
    shared = nested_dicts_are_shared()
    # then
    assert shared is False


def test_configured_databases_shares_leaves_that_cannot_be_deep_copied():
    # when
    facts = uncopiable_snapshot_facts()
    # then
    assert facts["leaf_is_shared"] is True


def test_configured_databases_copies_the_options_dict_around_those_leaves():
    # when
    facts = uncopiable_snapshot_facts()
    # then
    assert facts["options_dict_is_copied"] is True


def test_configured_databases_copies_nested_lists_around_those_leaves():
    # when
    facts = uncopiable_snapshot_facts()
    # then
    assert facts["nested_list_is_copied"] is True
