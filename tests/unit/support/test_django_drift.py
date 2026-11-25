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
    pyproject_text,
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
        '[[dependency]]\npackage = "p"\nnames = ["m.D", "m.E"]\n'
    )
    # when
    count = raw_entry_count(text)
    # then
    assert count == 5


PIN = "django>=6.1,<6.2"
C61 = ["Framework :: Django", "Framework :: Django :: 6.1"]
C62 = [*C61, "Framework :: Django :: 6.2"]
NO_REQUIREMENT = 'name = "demo"'
BARE = "[project]\ndependencies = [{}]\n"


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        (pyproject_text(PIN, C61), PIN),
        (pyproject_text("Django >=6.1, <6.2", C61), "Django >=6.1, <6.2"),
        (pyproject_text("django[argon2]>=6.1,<6.2", C61), "django[argon2]>=6.1,<6.2"),
        (pyproject_text("django_stubs>=1", C61), None),
        (pyproject_text("djangorestframework>=3.15", C61), None),
        (pyproject_text("django>=6.1,<6.2; python_version<'3.12'", C61), None),
        (BARE.format('"django<5; python_version<\'3\'", "django>=6.1,<6.2"'), "django>=6.1,<6.2"),
        (BARE.format('1, "django>=6.1,<6.2"'), "django>=6.1,<6.2"),
        (BARE.format('"django>=5", "django>=6.1,<6.2"'), "django<6.2,>=5,>=6.1"),
        (f"[project]\n{NO_REQUIREMENT}\n", None),
        ("[project]\ndependencies = 1\n", None),
        ("[project]\ndependencies = [1]\n", None),
        ('project = "x"\n', None),
        ("[project\n", None),
        ("x = " + "[" * 100000, None),
        ('[project]\nname = "demo"\n[dependency-groups]\ndev = ["django>=6.1,<6.2"]\n', None),
        (
            '[project]\nname = "demo"\n[project.optional-dependencies]\nx = ["django>=6.1,<6.2"]\n',
            None,
        ),
    ],
    ids=[
        "plain",
        "normalized-name",
        "extras",
        "similar-name",
        "unrelated-package",
        "marker-guarded-only",
        "marker-entry-then-plain",
        "non-string-entry-then-plain",
        "duplicate-entries-are-combined",
        "no-dependencies",
        "dependencies-not-a-list",
        "dependencies-without-strings",
        "project-not-a-table",
        "malformed-toml",
        "deeply-nested-toml",
        "dependency-group-ignored",
        "optional-dependency-ignored",
    ],
)
def test_django_requirement_is_read_from_the_project_dependencies(text, expected):
    # when
    requirement = django_requirement(text)
    # then
    assert requirement == expected


@pytest.mark.parametrize(
    ("requirement", "installed"),
    [
        (PIN, "6.1.0"),
        (PIN, "6.1.3"),
        (PIN, "6.1.1rc1"),
        ("django>=6.1.2,<6.2", "6.1.3"),
    ],
    ids=["lower-bound", "patch", "prerelease-inside-range", "patch-floor"],
)
def test_pin_check_accepts_an_installed_version_inside_the_range(requirement, installed):
    # when
    problem = pin_problem(requirement, installed)
    # then
    assert problem is None


@pytest.mark.parametrize(
    ("requirement", "installed", "fragment"),
    [
        (PIN, "6.2.0", "outside the pyproject pin"),
        (PIN, "6.2.0a1", "outside the pyproject pin"),
        (PIN, "6.0.9", "outside the pyproject pin"),
        (PIN, "6.1.0rc1", "outside the pyproject pin"),
        (None, "6.1.3", "no unconditional django requirement"),
        ("django", "6.1.3", "does not constrain"),
        ("django @ https://example.org/django.zip", "6.1.3", "does not constrain"),
        ("not a requirement!!", "6.1.3", "'not a requirement!!' / '6.1.3'"),
        (PIN, "abc", "'django>=6.1,<6.2' / 'abc'"),
    ],
    ids=[
        "above-upper-bound",
        "prerelease-of-next-minor",
        "below-lower-bound",
        "prerelease-below-lower-bound",
        "no-requirement",
        "unconstrained",
        "url-requirement",
        "invalid-requirement",
        "invalid-version",
    ],
)
def test_pin_check_reports_why_the_installed_version_does_not_satisfy_the_pin(
    requirement, installed, fragment
):
    # when
    problem = pin_problem(requirement, installed)
    # then
    assert problem is not None
    assert fragment in problem


@pytest.mark.parametrize(
    ("text", "installed"),
    [
        (pyproject_text(PIN, C61), "6.1.3"),
        (pyproject_text("django>=6.1,<6.3", C62), "6.2.0"),
        (pyproject_text(PIN, [*C61, "Programming Language :: Python :: 3"]), "6.1.3"),
        (pyproject_text(PIN, [*C61, "Framework :: Django :: 6"]), "6.1.3"),
        (pyproject_text("django>=6.1.2,<6.2", C61), "6.1.3"),
        (pyproject_text("django>6.1,<6.2", C61), "6.1.3"),
        (pyproject_text("django~=6.1.2", C61), "6.1.3"),
        (pyproject_text("django==6.1.3", C61), "6.1.3"),
        (pyproject_text("django==6.1.*", C61), "6.1.3"),
        (pyproject_text("django>=6.1,<6.3,!=6.2.*", C61), "6.1.3"),
        (pyproject_text("django>=6.1.7,<6.3", C62), "6.2.0"),
        (pyproject_text("django>=6.1,<=6.2.5", C62), "6.1.3"),
        (pyproject_text("django>=6.1,<6.3,!=6.2.0.*,!=6.2.1.*,!=6.2.2.*", C62), "6.1.3"),
        (pyproject_text("django>=6.1,<=6.2", C62), "6.2.0"),
        (pyproject_text("django>=6.1,<6.2.5", C62), "6.2.0"),
        (pyproject_text(PIN, [*C61, "Framework :: Django :: 6.1.2"]), "6.1.3"),
        (
            BARE.format('"django>=6.1", "django<6.2"')
            + 'classifiers = ["Framework :: Django :: 6.1"]\n',
            "6.1.3",
        ),
        (pyproject_text("django>=6.1,<6.2,!=7.0.*", C61), "6.1.3"),
        (pyproject_text("django>=6.1,<6.2,!=6.99999999", C61), "6.1.3"),
        (pyproject_text(["django>=5", "django>=6.1,<6.2"], C61), "6.1.3"),
        (pyproject_text("django>=6.1,<=6.2rc1", C61), "6.1.3"),
        (pyproject_text(["django>=6.1,<7", "django<6.2"], C61), "6.1.3"),
        (pyproject_text("django>=6.1,<6.2,!=6.1.0,!=6.1.1", C61), "6.1.3"),
        (pyproject_text("django>=6.1,<6.2,!=1!6.1.5", C61), "6.1.3"),
        (pyproject_text("django>=6.1,<6.2,!=6.1.0.*,!=6.1.1.*", C61), "6.1.3"),
        (pyproject_text("django>=6.1,<6.2,!=6.1.0.*,!=6.1.1.*,!=6.1.2.*", C61), "6.1.3"),
        (pyproject_text("django>6.1.3,<6.2", C61), "6.1.4"),
        (pyproject_text("django>=6.1.7,<6.2", C61), "6.1.7"),
        (pyproject_text("django==6.1.7", C61), "6.1.7"),
        (pyproject_text("django>6.1.3,<=6.1.9,!=6.1.3.1", C61), "6.1.9"),
        (pyproject_text("django>=6.1.4,!=6.1.4.*,<6.2", C61), "6.1.5"),
        (pyproject_text("django~=6.1.4rc1,<=6.1.4", C61), "6.1.4"),
        (pyproject_text(PIN, [*C61, "Framework :: Django :: \u0666.\u0661"]), "6.1.3"),
        (
            pyproject_text(
                "django>=6.9,<6.11", ["Framework :: Django :: 6.9", "Framework :: Django :: 6.10"]
            ),
            "6.9.1",
        ),
    ],
    ids=[
        "single",
        "widened-range",
        "unrelated-classifiers",
        "major-only-classifier-ignored",
        "patch-floor",
        "exclusive-lower-bound",
        "compatible-release",
        "exact-version",
        "wildcard-minor",
        "excluded-minor",
        "lower-bound-only-reaches-its-minor",
        "upper-bound-only-reaches-its-minor",
        "patch-three-candidate",
        "inclusive-upper-bound",
        "patch-upper-bound",
        "three-part-classifier-ignored",
        "split-entries-are-combined",
        "exclusion-outside-range",
        "huge-exclusion",
        "redundant-lower-bound",
        "prerelease-upper-bound-does-not-admit-its-minor",
        "redundant-upper-bound",
        "two-patches-excluded",
        "epoch-only-in-an-exclusion",
        "wildcard-patches-excluded",
        "three-wildcard-patches-excluded",
        "exclusive-lower-patch-bound",
        "lower-bound-candidate",
        "exact-patch-candidate",
        "upper-bound-candidate",
        "installed-version-candidate",
        "base-version-candidate",
        "non-ascii-digit-classifier-ignored",
        "two-digit-minor",
    ],
)
def test_classifier_check_accepts_classifiers_matching_the_pin(text, installed):
    # when
    problem = classifier_problem(text, installed)
    # then
    assert problem is None


@pytest.mark.parametrize(
    ("text", "installed", "fragment"),
    [
        (pyproject_text(PIN, [*C61, "Framework :: Django :: 6.0"]), "6.1.3", "['6.0'] is outside"),
        (pyproject_text(PIN, ["Framework :: Django :: 6.0"]), "6.1.3", "['6.0'] is outside"),
        (
            pyproject_text("django>=6.1,<6.3", C61),
            "6.2.0",
            "no classifier 'Framework :: Django :: 6.2'",
        ),
        (
            pyproject_text("django>=6.1,<6.3", C61),
            "6.1.3",
            "no classifier 'Framework :: Django :: 6.2'",
        ),
        (
            pyproject_text("django>=6.8,<6.11", ["Framework :: Django :: 6.8"]),
            "6.8.1",
            "'Framework :: Django :: 6.9', 'Framework :: Django :: 6.10'",
        ),
        (
            pyproject_text(
                PIN, [*C61, "Framework :: Django :: 6.9", "Framework :: Django :: 6.10"]
            ),
            "6.1.3",
            "['6.9', '6.10'] are outside",
        ),
        (
            pyproject_text("django>=6.0,<6.50", ["Framework :: Django :: 6.0"]),
            "6.1.3",
            "no classifiers",
        ),
        (
            pyproject_text(PIN, [*C61, "Framework :: Django :: " + "9" * 5000 + ".1"]),
            "6.1.3",
            "is outside",
        ),
        (
            pyproject_text("django>=6.1,<6.4", C61),
            "6.1.3",
            "no classifiers 'Framework :: Django :: 6.2', 'Framework :: Django :: 6.3'",
        ),
        (
            pyproject_text(PIN, [*C61[:1], "Framework :: Django :: 6.1 "]),
            "6.1.3",
            "no classifier 'Framework :: Django :: 6.1'",
        ),
        (
            pyproject_text(PIN, ["Framework :: Django"]),
            "6.1.3",
            "no classifier 'Framework :: Django :: 6.1'",
        ),
        (
            BARE.format('"django>=6.1,<6.2"') + "classifiers = [1]\n",
            "6.1.3",
            "no classifier 'Framework :: Django :: 6.1'",
        ),
        (BARE.format('"django>=6.1,<6.2"'), "6.1.3", "no classifier 'Framework :: Django :: 6.1'"),
    ],
    ids=[
        "classifier-outside-range",
        "stale-and-missing-reports-stale",
        "installed-minor-missing",
        "new-minor-missing-while-older-installed",
        "numeric-minor-order",
        "numeric-stale-order",
        "span-of-50-is-enumerated",
        "huge-integer-classifier",
        "several-minors-missing",
        "trailing-space-classifier-does-not-count",
        "bare-classifier-only",
        "non-string-classifier",
        "no-classifiers-key",
    ],
)
def test_classifier_check_reports_stale_or_missing_classifiers(text, installed, fragment):
    # when
    problem = classifier_problem(text, installed)
    # then
    assert problem is not None
    assert fragment in problem


@pytest.mark.parametrize(
    ("requirement", "fragment"),
    [
        ("django>=6.1", "needs both a lower and an upper bound"),
        ("django<6.2", "needs both a lower and an upper bound"),
        ("django>=5.2,<6.2", "spans several major versions"),
        ("django>=6.1,<7", "spans several major versions"),
        ("django>=6.1,<6.99999999", "spans more than"),
        ("django~=6.1", "two-part ~="),
        ("django===6.1.3", "'django===6.1.3': arbitrary equality"),
        ("django>=6.0,<6.51", "spans more than 50"),
        ("django==6.*", "major-only == wildcard"),
    ],
    ids=[
        "no-upper-bound",
        "no-lower-bound",
        "several-majors",
        "next-major-bound",
        "huge-upper-bound",
        "span-of-51",
        "two-part-compatible-release",
        "arbitrary-equality",
        "major-only-wildcard",
    ],
)
def test_classifier_check_reports_a_pin_whose_minors_cannot_be_enumerated(requirement, fragment):
    # given
    text = pyproject_text(requirement, C61)
    # when
    problem = classifier_problem(text, "6.1.3")
    # then
    assert problem is not None
    assert fragment in problem


@pytest.mark.parametrize(
    ("text", "installed", "fragment"),
    [
        (pyproject_text(PIN, C61), "6.2.0", "outside the pyproject pin"),
        (f"[project]\n{NO_REQUIREMENT}\n", "6.1.3", "no unconditional django requirement"),
        ("[project\n", "6.1.3", "no unconditional django requirement"),
        (
            pyproject_text("django>=6.1,<=6.1.*", C61),
            "6.1.3",
            "no unconditional django requirement",
        ),
        (pyproject_text(PIN, C61), "abc", "'django>=6.1,<6.2' / 'abc'"),
        (BARE.format('"django>=6.1,<6.2"') + "classifiers = 1\n", "6.1.3", "is not a list"),
    ],
    ids=[
        "installed-outside-pin",
        "no-requirement",
        "malformed-toml",
        "invalid-requirement-entry",
        "invalid-installed-version",
        "classifiers-not-a-list",
    ],
)
def test_classifier_check_reports_an_invalid_pyproject_or_pin(text, installed, fragment):
    # when
    problem = classifier_problem(text, installed)
    # then
    assert problem is not None
    assert fragment in problem


def test_classifier_check_reports_a_pin_with_a_non_zero_epoch():
    # given
    text = pyproject_text("django>=1!6.1,<1!6.2", C61)
    # when
    problem = classifier_problem(text, "1!6.1.3")
    # then
    assert problem is not None
    assert "non-zero epoch" in problem


def test_classifier_check_does_not_find_a_minor_reachable_only_through_unlisted_patches():
    # given: documented limit of _admitted_minors, pinned so changing it is deliberate
    text = pyproject_text("django>=6.1,<6.3,!=6.2.0.*,!=6.2.1.*,!=6.2.2.*,!=6.2.3.*", C61)
    # when
    problem = classifier_problem(text, "6.1.3")
    # then
    assert problem is None


AS = "asgiref>=3.13"
VERSIONS = frozenset({("asgiref", "3.13.0")})


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        (pyproject_text(AS, C61), AS),
        (pyproject_text(["django>=6.1,<6.2", AS], C61), AS),
        (pyproject_text("ASGIref>=3.13", C61), "ASGIref>=3.13"),
        (pyproject_text("asgiref_extra>=1", C61), None),
        (pyproject_text("asgiref>=3.13; python_version<'3.12'", C61), None),
        (pyproject_text("django>=6.1,<6.2", C61), None),
        (BARE.format('"asgiref>=3.9", "asgiref>=3.13"'), "asgiref>=3.13,>=3.9"),
    ],
    ids=[
        "plain",
        "next-to-django",
        "normalized-name",
        "similar-name",
        "marker-only",
        "absent",
        "combined",
    ],
)
def test_package_requirement_is_read_by_name(text, expected):
    # when
    requirement = package_requirement(text, "asgiref")
    # then
    assert requirement == expected


@pytest.mark.parametrize(
    ("requirement", "installed", "fragment"),
    [
        (None, "3.13.0", "no unconditional asgiref requirement"),
        ("asgiref>=3.13", "3.12.9", "installed asgiref 3.12.9 is outside"),
        ("asgiref", "3.13.0", "does not constrain"),
    ],
    ids=["no-requirement", "below-floor", "unconstrained"],
)
def test_pin_check_names_the_package_it_was_given(requirement, installed, fragment):
    # when
    problem = pin_problem(requirement, installed, "asgiref")
    # then
    assert problem is not None
    assert fragment in problem


def test_pin_check_accepts_an_installed_version_at_the_floor_of_another_package():
    # when
    problem = pin_problem(AS, "3.13.0", "asgiref")
    # then
    assert problem is None


@pytest.mark.parametrize(
    "installed", ["3.13.0", "3.13.0.0"], ids=["same-string", "same-version-other-spelling"]
)
def test_ledger_version_check_accepts_the_exact_version(installed):
    # when
    problem = ledger_version_problem(VERSIONS, "asgiref", installed)
    # then
    assert problem is None


@pytest.mark.parametrize(
    ("versions", "installed", "fragment"),
    [
        (VERSIONS, "3.13.1", "installed asgiref 3.13.1 differs from the ledger's 3.13.0"),
        (VERSIONS, "3.12.0", "differs from the ledger's 3.13.0"),
        (VERSIONS, "3.14.0", "differs from the ledger's 3.13.0"),
        (VERSIONS, "3.13.0rc1", "differs from the ledger's 3.13.0"),
        (frozenset(), "3.13.0", "no [[dependency]] row for asgiref"),
        (VERSIONS, "abc", "'abc'"),
        (frozenset({("asgiref", "x.y")}), "3.13.0", "'x.y'"),
    ],
    ids=[
        "patch-ahead",
        "older",
        "newer-minor",
        "prerelease",
        "no-row",
        "bad-installed",
        "bad-ledger",
    ],
)
def test_ledger_version_check_reports_why_the_installed_version_is_not_the_verified_one(
    versions, installed, fragment
):
    # when
    problem = ledger_version_problem(versions, "asgiref", installed)
    # then
    assert problem is not None
    assert fragment in problem


def test_ledger_pin_check_accepts_a_verified_version_inside_the_requirement():
    # when
    problem = ledger_pin_problem(pyproject_text(AS, C61), VERSIONS, "asgiref")
    # then
    assert problem is None


@pytest.mark.parametrize(
    ("text", "versions", "fragment"),
    [
        (pyproject_text("asgiref>=3.14", C61), VERSIONS, "ledger's asgiref 3.13.0 is outside"),
        (pyproject_text("django>=6.1,<6.2", C61), VERSIONS, "no unconditional asgiref requirement"),
        (pyproject_text("asgiref", C61), VERSIONS, "does not constrain"),
        (pyproject_text(AS, C61), frozenset(), "no [[dependency]] row for asgiref"),
        (
            pyproject_text("not a requirement!!", C61),
            VERSIONS,
            "no unconditional asgiref requirement",
        ),
    ],
    ids=[
        "verified-below-floor",
        "no-requirement",
        "unconstrained",
        "no-row",
        "invalid-requirement",
    ],
)
def test_ledger_pin_check_reports_why_the_verified_version_is_not_allowed(text, versions, fragment):
    # when
    problem = ledger_pin_problem(text, versions, "asgiref")
    # then
    assert problem is not None
    assert fragment in problem


@pytest.mark.parametrize(
    ("package", "name"),
    [("asgiref", "local.Local"), ("asgiref", "sync.sync_to_async"), ("asgiref", "local")],
    ids=["class", "function", "module"],
)
def test_dependency_name_check_accepts_names_below_the_package(package, name):
    # when
    problem = dependency_name_problem(package, name)
    # then
    assert problem is None


@pytest.mark.parametrize(
    ("package", "name", "fragment"),
    [
        ("asgiref", "local.NoSuchThing", "not found"),
        ("asgiref", "no_such_module.X", "not found"),
        ("no_such_package_zzz", "x", "not importable"),
    ],
    ids=["missing-attribute", "missing-module", "missing-package"],
)
def test_dependency_name_check_reports_a_name_that_is_gone(package, name, fragment):
    # when
    problem = dependency_name_problem(package, name)
    # then
    assert problem is not None
    assert fragment in problem
