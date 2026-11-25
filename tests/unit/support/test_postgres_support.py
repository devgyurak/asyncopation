"""`patched_alias_settings` and its helpers: overrides are visible inside, never leak out."""

from tests.support.lifecycle.postgres import alias_value_during_and_after, other_alias_value_during


def test_patched_alias_settings_applies_the_override_inside_the_block(fake):
    # when
    _before, during, _after = alias_value_during_and_after("PASSWORD", "patched-secret")
    # then
    assert during == "patched-secret"


def test_patched_alias_settings_restores_the_original_value_afterwards(fake):
    # when
    before, _during, after = alias_value_during_and_after("PASSWORD", "patched-secret")
    # then
    assert after == before
    assert after != "patched-secret"


def test_patched_alias_settings_leaves_the_other_aliases_alone(fake):
    # when
    before, during = other_alias_value_during("PASSWORD", "patched-secret", "default", "other")
    # then
    assert during == before
