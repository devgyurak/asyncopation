"""The ledger is the single source of the Django drift list (ADR-0007 Decision 6)."""

from importlib import metadata

import django
import pytest

from tests.support.django_drift import (
    attribute_problem,
    attribute_signature_problem,
    classifier_problem,
    dependency_name_problem,
    django_requirement,
    import_problem,
    ledger_pin_problem,
    ledger_version_problem,
    package_requirement,
    pin_problem,
    ported_problem,
    raw_entry_count,
    signature_problem,
)
from tools.guards.archlint import LEDGER_PATH, ROOT, Ledger

LEDGER = Ledger.load()
IMPORTED = sorted(LEDGER.internal - LEDGER.ported)


def test_every_written_ledger_entry_is_loaded():
    # given
    written = raw_entry_count((ROOT / LEDGER_PATH).read_text(encoding="utf-8"))
    # when
    loaded = len(LEDGER.internal) + len(LEDGER.attributes) + len(LEDGER.dependencies)
    # then
    assert loaded == written


def test_installed_django_satisfies_the_pyproject_pin():
    # given
    pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    # when
    problem = pin_problem(django_requirement(pyproject), django.__version__)
    # then
    assert problem is None


def test_django_classifiers_match_the_pyproject_pin():
    # given
    pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    # when
    problem = classifier_problem(pyproject, django.__version__)
    # then
    assert problem is None


def test_installed_asgiref_is_the_version_the_ledger_was_verified_against():
    # when
    problem = ledger_version_problem(
        LEDGER.dependency_versions, "asgiref", metadata.version("asgiref")
    )
    # then
    assert problem is None


def test_installed_asgiref_satisfies_the_pyproject_requirement():
    # given
    pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    # when
    problem = pin_problem(
        package_requirement(pyproject, "asgiref"), metadata.version("asgiref"), "asgiref"
    )
    # then
    assert problem is None


def test_asgiref_version_in_the_ledger_satisfies_the_pyproject_requirement():
    # given
    pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    # when
    problem = ledger_pin_problem(pyproject, LEDGER.dependency_versions, "asgiref")
    # then
    assert problem is None


@pytest.mark.parametrize(("package", "name"), sorted(LEDGER.dependencies))
def test_ledgered_dependency_name_still_exists(package, name):
    # when
    problem = dependency_name_problem(package, name)
    # then
    assert problem is None


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
