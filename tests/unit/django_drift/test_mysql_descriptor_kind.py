"""``kind_of`` names a cached property the same whichever decorator class Django uses."""

import pytest

from tests.support.cached_property_samples import (
    DjangoSample,
    FunctoolsSample,
    PlainMethodSample,
    PlainPropertySample,
    RenamedSample,
)
from tests.support.mysql_drift import kind_of


@pytest.mark.parametrize(
    "owner", [FunctoolsSample, DjangoSample, RenamedSample], ids=["functools", "django", "renamed"]
)
def test_every_cached_property_decorator_class_is_the_same_kind(owner):
    # when
    kind = kind_of(owner, "value")
    # then
    assert kind == "cached_property"


@pytest.mark.parametrize(
    ("owner", "name", "expected"),
    [
        (PlainPropertySample, "value", "property"),
        (PlainMethodSample, "value", "function"),
        (PlainMethodSample, "missing", "absent"),
    ],
    ids=["property", "method", "absent"],
)
def test_other_descriptors_keep_their_own_kind(owner, name, expected):
    # when
    kind = kind_of(owner, name)
    # then
    assert kind == expected
