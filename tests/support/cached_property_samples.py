"""Sample classes for the cached-property drift helper unit tests."""

from __future__ import annotations

import functools
import warnings

from tests.support.connection_drift import legacy_cached_property


class FunctoolsSample:
    @functools.cached_property
    def value(self) -> int:
        return 1


def _make_django_sample() -> type:
    """A class using Django's own ``cached_property`` (functools' when Django no longer ships it).

    Django 6.1 (the pinned version) uses the legacy decorator. Django main deprecates it (ticket
    #30949, ``RemovedInDjango2029Warning`` when used as a decorator), which must not fail the
    canary, so the warning is silenced only while the class is defined.
    """
    decorator = legacy_cached_property() or functools.cached_property
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")

        class DjangoSample:
            @decorator
            def value(self) -> int:
                return 1

    return DjangoSample


DjangoSample = _make_django_sample()


class InheritedSample(FunctoolsSample):
    pass


class PlainPropertySample:
    @property
    def value(self) -> int:
        return 1


class PlainMethodSample:
    def value(self) -> int:
        return 1


class LegacyMarker:
    """Stands in for a legacy decorator class in helper unit tests."""
