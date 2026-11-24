"""The ledger is the single source of the Django drift list (ADR-0007 Decision 6)."""

import django
import pytest

from tests.support.django_drift import (
    PINNED_DJANGO,
    attribute_problem,
    import_problem,
    signature_problem,
)
from tools.guards.archlint import Ledger

LEDGER = Ledger.load()


def test_django_is_the_pinned_minor():
    # when
    installed = django.VERSION[:2]
    # then
    assert installed == PINNED_DJANGO


@pytest.mark.parametrize(("module", "name"), sorted(LEDGER.internal))
def test_ledgered_internal_is_importable(module, name):
    # when
    problem = import_problem(module, name)
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


@pytest.mark.parametrize(
    ("owner", "name"),
    [
        ("django.db.models.query.QuerySet", "filter"),
        ("django.db.models.query.QuerySet", "_prefetch_related_lookups"),
        ("django.db.models.query.QuerySet", "_hints"),
    ],
    ids=["class-attribute", "instance-attribute", "second-instance-attribute"],
)
def test_attribute_check_accepts_class_and_instance_attributes(owner, name):
    # when
    problem = attribute_problem(owner, name)
    # then
    assert problem is None


@pytest.mark.parametrize(
    ("owner", "name"),
    [
        ("django.db.models.query.QuerySet", "_no_such_attribute"),
        ("django.db.models.query.NoSuchClass", "_x"),
    ],
    ids=["missing-name", "missing-owner"],
)
def test_attribute_check_reports_missing_attribute_or_owner(owner, name):
    # when
    problem = attribute_problem(owner, name)
    # then
    assert problem is not None


@pytest.mark.parametrize(
    ("module", "name"),
    [("django.db.models.query", "NoSuchName"), ("django.no_such_module", "*")],
    ids=["missing-name", "missing-module"],
)
def test_import_check_reports_missing_name_or_module(module, name):
    # when
    problem = import_problem(module, name)
    # then
    assert problem is not None


@pytest.mark.parametrize(
    ("module", "name"),
    [
        ("django.db.models.query", "ModelIterable"),
        ("django.db.models", "sql"),
        ("django.conf", "*"),
    ],
    ids=["class", "submodule", "module-only"],
)
def test_import_check_accepts_existing_names_and_submodules(module, name):
    # when
    problem = import_problem(module, name)
    # then
    assert problem is None


def test_attribute_check_reports_an_owner_that_is_not_a_class():
    # when
    problem = attribute_problem("django.db.models.query.prefetch_related_objects", "x")
    # then
    assert problem is not None


def test_signature_check_reports_a_changed_signature():
    # when
    problem = signature_problem("django.db.models.query", "ModelIterable", "(nothing)")
    # then
    assert problem is not None
