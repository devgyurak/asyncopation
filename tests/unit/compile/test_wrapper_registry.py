"""`wrapper_for` keeps a structural copy of the settings (ADR-0008)."""

import pytest

from tests.support.wrapper_registry import (
    MUTATIONS,
    reuse_with_fresh_dict,
    reuse_with_ordered_dicts,
    reuse_with_same_dict,
    reused_after_mutation,
    reused_after_ordered_dict_mutation,
)


def test_unusual_option_values_do_not_break_wrapper_reuse_with_the_same_dict(alias):
    # when
    reused = reuse_with_same_dict(alias)
    # then
    assert reused is True


def test_a_fresh_dict_holding_the_same_leaf_objects_reuses_the_wrapper(alias):
    # when
    reused = reuse_with_fresh_dict(alias)
    # then
    assert reused is True


@pytest.mark.parametrize("mutation", sorted(MUTATIONS))
def test_changing_the_callers_settings_yields_a_fresh_wrapper(alias, mutation):
    # when
    reused = reused_after_mutation(alias, mutation)
    # then
    assert reused is False


def test_dict_subclasses_still_compare_equal_and_reuse_the_wrapper(alias):
    # when
    reused = reuse_with_ordered_dicts(alias)
    # then
    assert reused is True


def test_mutating_an_ordered_dict_in_place_yields_a_fresh_wrapper(alias):
    # when
    reused = reused_after_ordered_dict_mutation(alias)
    # then
    assert reused is False
