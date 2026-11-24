"""The ledger is the single source of the Django drift list (ADR-0007 Decision 6)."""

import django
import pytest

from tests.support.django_drift import (
    PINNED_DJANGO,
    attribute_problem,
    attribute_signature_problem,
    import_problem,
    ported_problem,
    signature_problem,
)
from tools.guards.archlint import Ledger

LEDGER = Ledger.load()
IMPORTED = sorted(LEDGER.internal - LEDGER.ported)


def test_django_is_the_pinned_minor():
    # when
    installed = django.VERSION[:2]
    # then
    assert installed == PINNED_DJANGO


@pytest.mark.parametrize(("module", "name"), IMPORTED)
def test_ledgered_internal_is_importable(module, name):
    # when
    problem = import_problem(module, name)
    # then
    assert problem is None


@pytest.mark.parametrize(("module", "name"), sorted(LEDGER.ported))
def test_ledgered_ported_item_is_still_defined_in_the_django_source(module, name):
    # when
    problem = ported_problem(module, name)
    # then
    assert problem is None


@pytest.mark.parametrize(("module", "name", "expected"), sorted(LEDGER.signatures))
def test_ledgered_signature_matches_installed_django(module, name, expected):
    # when
    problem = signature_problem(module, name, expected)
    # then
    assert problem is None


@pytest.mark.parametrize(("owner", "name"), sorted(LEDGER.attributes))
def test_ledgered_private_attribute_still_exists(owner, name):
    # when
    problem = attribute_problem(owner, name)
    # then
    assert problem is None


@pytest.mark.parametrize(("owner", "name", "expected"), sorted(LEDGER.attribute_signatures))
def test_ledgered_attribute_signature_matches_installed_django(owner, name, expected):
    # when
    problem = attribute_signature_problem(owner, name, expected)
    # then
    assert problem is None
