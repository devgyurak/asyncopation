import pytest

from tests.support.django_drift import (
    attribute_problem,
    attribute_signature_problem,
    import_problem,
    ported_problem,
    raw_entry_count,
    signature_problem,
)

SAMPLE = "tests.support.drift_samples"


@pytest.mark.parametrize(
    ("owner", "name"),
    [
        ("django.db.models.query.QuerySet", "filter"),
        ("django.db.models.query.QuerySet", "_prefetch_related_lookups"),
        ("django.db.models.query.ModelIterable", "queryset"),
        (f"{SAMPLE}.Sample", "class_level"),
        (f"{SAMPLE}.Sample", "stored"),
        (f"{SAMPLE}.Child", "stored"),
        ("django.db.models.Model", "_meta"),
    ],
    ids=[
        "class-attribute",
        "instance-attribute",
        "inherited-instance-attribute",
        "sample-class-attribute",
        "sample-stored-in-init",
        "sample-stored-in-base-class",
        "metaclass-installed-attribute",
    ],
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
        ("django.db.models.Model", "Meta"),
        ("django.db.models.Model", "no_such_zzz"),
        ("django.db.models.query.NoSuchClass", "_x"),
        ("django.db.models.query.prefetch_related_objects", "x"),
        ("NoModulePath", "x"),
        (f"{SAMPLE}.Sample", "read_only"),
    ],
    ids=[
        "missing-name",
        "metaclass-owner-string-literal-only",
        "metaclass-owner-missing-name",
        "missing-owner",
        "owner-not-a-class",
        "no-module-path",
        "read-not-store",
    ],
)
def test_attribute_check_reports_missing_attribute_or_owner(owner, name):
    # when
    problem = attribute_problem(owner, name)
    # then
    assert problem is not None


@pytest.mark.parametrize(
    ("module", "name"),
    [
        ("django.db.models.query", "NoSuchName"),
        ("django.no_such_module", "*"),
        ("tests.support.drift_broken", "*"),
    ],
    ids=["missing-name", "missing-module", "import-raises"],
)
def test_import_check_reports_missing_name_or_module(module, name):
    # when
    problem = import_problem(module, name)
    # then
    assert problem is not None


@pytest.mark.parametrize(
    ("module", "name"),
    [("django.db.models.query", "ModelIterable"), ("django.conf", "*")],
    ids=["class", "module-only"],
)
def test_import_check_accepts_existing_names(module, name):
    # when
    problem = import_problem(module, name)
    # then
    assert problem is None


def test_signature_check_accepts_a_matching_signature():
    # when
    problem = signature_problem(
        SAMPLE, "method_with_signature", "(a: 'int', b: 'int' = 2) -> 'int'"
    )
    # then
    assert problem is None


@pytest.mark.parametrize(
    ("module", "name"),
    [
        ("django.db.models.query", "ModelIterable"),
        ("django.no_such_module", "X"),
        (SAMPLE, "nope"),
        (SAMPLE, "CONSTANT"),
    ],
    ids=["changed", "missing-module", "missing-name", "not-callable"],
)
def test_signature_check_reports_a_changed_or_unavailable_signature(module, name):
    # when
    problem = signature_problem(module, name, "(nothing)")
    # then
    assert problem is not None


def test_signature_check_follows_dotted_method_names():
    # when
    problem = signature_problem(
        "django.db.models.sql.query",
        "Query.get_compiler",
        "(self, using=None, connection=None, elide_empty=True)",
    )
    # then
    assert problem is None


def test_attribute_signature_check_resolves_a_method_on_the_owner_class():
    # when
    problem = attribute_signature_problem(f"{SAMPLE}.Sample", "read", "(self) -> 'int'")
    # then
    assert problem is None


@pytest.mark.parametrize(
    "name",
    ["DatabaseWrapper", "DatabaseWrapper.operators", "DatabaseWrapper._data_types"],
    ids=["class", "class-attribute", "private-class-attribute"],
)
def test_ported_check_reads_the_source_without_importing_the_driver(name):
    # when
    problem = ported_problem("django.db.backends.mysql.base", name)
    # then
    assert problem is None


def test_ported_check_reports_a_name_missing_from_the_source():
    # when
    problem = ported_problem("django.db.backends.mysql.base", "DatabaseWrapper.no_such_member")
    # then
    assert problem is not None


def test_attribute_signature_check_reports_a_changed_signature():
    # when
    problem = attribute_signature_problem(f"{SAMPLE}.Sample", "read", "(self, extra)")
    # then
    assert problem is not None
    assert "ledger records (self, extra)" in problem


@pytest.mark.parametrize(
    ("module", "name"),
    [
        ("no_such_top_level_module_xyz", "X"),
        ("django.no_such_module", "X"),
        ("no_such_parent.child", "X"),
        ("tests.support.drift_broken", "X"),
    ],
    ids=["no-spec", "missing-submodule", "find-spec-raises", "source-without-the-name"],
)
def test_ported_check_reports_an_unlocatable_module(module, name):
    # when
    problem = ported_problem(module, name)
    # then
    assert problem is not None


@pytest.mark.parametrize(
    "name",
    [
        "Sample",
        "Sample.read",
        "Sample.read_only",
        "Sample.class_level",
        "method_with_signature",
        "CONSTANT",
    ],
    ids=["class", "method", "annotation", "assignment", "function", "constant"],
)
def test_ported_check_finds_methods_annotations_and_module_level_names(name):
    # when
    problem = ported_problem(SAMPLE, name)
    # then
    assert problem is None


def test_raw_entry_count_counts_every_written_name():
    # given
    text = (
        "[public]\nmodules = []\n"
        '[[internal]]\nmodule = "m"\nnames = ["A", "B"]\n'
        '[[attribute]]\nowner = "o.C"\nnames = ["_x"]\n'
    )
    # when
    count = raw_entry_count(text)
    # then
    assert count == 3
