import functools

import pytest

from tests.support import connection_drift
from tests.support.cached_property_samples import (
    DjangoSample,
    FunctoolsSample,
    InheritedSample,
    LegacyMarker,
    PlainMethodSample,
    PlainPropertySample,
)
from tests.support.connection_drift import static_is_cached_property


@pytest.mark.parametrize(
    ("owner", "expected"),
    [
        (FunctoolsSample, True),
        (DjangoSample, True),
        (InheritedSample, True),
        (PlainPropertySample, False),
        (PlainMethodSample, False),
    ],
    ids=["functools", "django", "inherited", "property", "method"],
)
def test_static_is_cached_property_matches_the_decorator_class(owner, expected):
    # when
    found = static_is_cached_property(owner, "value")
    # then
    assert found is expected


def test_cached_property_classes_without_the_legacy_class_is_functools_only(monkeypatch):
    # given
    monkeypatch.setattr(connection_drift, "legacy_cached_property", lambda: None)
    # when
    classes = connection_drift.cached_property_classes()
    # then
    assert classes == (functools.cached_property,)


def test_cached_property_classes_with_the_legacy_class_lists_both(monkeypatch):
    # given
    monkeypatch.setattr(connection_drift, "legacy_cached_property", lambda: LegacyMarker)
    # when
    classes = connection_drift.cached_property_classes()
    # then
    assert classes == (functools.cached_property, LegacyMarker)


def test_static_is_cached_property_on_a_missing_attribute_raises():
    # when / then
    with pytest.raises(AttributeError):
        static_is_cached_property(PlainMethodSample, "missing")
