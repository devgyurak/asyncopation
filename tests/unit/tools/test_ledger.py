import pytest

from tools.guards import archlint
from tools.guards.archlint import Ledger

BASE = '[public]\nmodules = ["django.db"]\n'
QUERY = "django.db.models.sql.query"
REC = 'used_by = ["src/asyncopation/_django/x.py"]\nreason = "r"\nverified_against = "6.1.3"\n'
FULL = (
    BASE
    + """
[[internal]]
module = "django.db.models.sql.query"
names = ["Query"]
used_by = ["src/asyncopation/_django/x.py"]
reason = "r"
verified_against = "6.1.3"
signatures = { "Query.get_compiler" = "(self, using=None)" }

[[internal]]
module = "django.db.backends.mysql.base"
names = ["DatabaseWrapper", "DatabaseWrapper.operators"]
used_by = ["src/asyncopation/_django/x.py"]
reason = "r"
verified_against = "6.1.3"
ported = true

[[attribute]]
owner = "django.db.models.query.QuerySet"
names = ["_fetch_mode"]
used_by = ["src/asyncopation/_django/x.py"]
reason = "r"
verified_against = "6.1.3"
signatures = { _fetch_mode = "()" }
"""
)


def test_internal_signatures_and_attribute_keys_are_parsed():
    # when
    ledger = Ledger.from_toml(FULL)
    # then
    assert (QUERY, "Query") in ledger.internal
    assert ledger.signatures == {(QUERY, "Query.get_compiler", "(self, using=None)")}
    assert ledger.attributes == {("django.db.models.query.QuerySet", "_fetch_mode")}
    assert ledger.attribute_signatures == {("django.db.models.query.QuerySet", "_fetch_mode", "()")}


DEP = (
    '[[dependency]]\npackage = "asgiref"\nnames = ["local.Local", "sync.sync_to_async"]\n'
    'used_by = ["src/asyncopation/_django/connections.py"]\nreason = "r"\n'
    'verified_against = "3.13.0"\n'
)


def test_dependency_rows_are_parsed_into_names_and_versions():
    # when
    ledger = Ledger.from_toml(BASE + DEP)
    # then
    assert ledger.dependencies == {("asgiref", "local.Local"), ("asgiref", "sync.sync_to_async")}
    assert ledger.dependency_versions == {("asgiref", "3.13.0")}


def test_dependency_rows_are_never_import_coverage_for_the_guards():
    # when
    ledger = Ledger.from_toml(BASE + DEP)
    # then
    assert (ledger.internal, ledger.covers("asgiref.local", "Local")) == (frozenset(), False)


def test_only_ported_entries_are_in_the_ported_set():
    # when
    ledger = Ledger.from_toml(FULL)
    # then
    mysql = "django.db.backends.mysql.base"
    assert ledger.ported == {(mysql, "DatabaseWrapper"), (mysql, "DatabaseWrapper.operators")}
    assert (QUERY, "Query") not in ledger.ported


def test_ported_used_by_holds_only_ported_entries_paths():
    # when
    ledger = Ledger.from_toml(FULL)
    # then
    assert ledger.ported_used_by == {
        ("django.db.backends.mysql.base", "src/asyncopation/_django/x.py")
    }


def test_wildcard_entry_covers_any_name_of_the_module():
    # given
    ledger = Ledger.from_toml(BASE + '[[internal]]\nmodule = "m"\nnames = ["*"]\n' + REC)
    # when
    covered = (ledger.covers("m", "Anything"), ledger.covers("other", "Anything"))
    # then
    assert covered == (True, False)


def test_ported_entries_do_not_cover_a_real_import():
    # given
    ledger = Ledger.from_toml(FULL)
    # when
    covered = (
        ledger.covers(QUERY, "Query"),
        ledger.covers("django.db.backends.mysql.base", "DatabaseWrapper"),
    )
    # then
    assert covered == (True, False)


@pytest.mark.parametrize(
    ("entry", "message"),
    [
        ('[[internal]]\nnames = ["X"]\n', "`module` must be a str"),
        ('[[internal]]\nmodule = 1\nnames = ["X"]\n', "`module` must be a str"),
        ('[[internal]]\nmodule = "m"\n', "`names` must be a non-empty list"),
        ('[[internal]]\nmodule = "m"\nnames = "X"\n', "`names` must be a non-empty list"),
        ('[[internal]]\nmodule = "m"\nnames = []\n', "`names` must be a non-empty list"),
        ('[[internal]]\nmodule = "m"\nnames = [1]\n', "`names` must be a non-empty list"),
        ('[[internal]]\nmodule = "m"\nnames = ["X", "X"]\n', "listed twice"),
        ('[[internal]]\nmodule = "m"\nnames = ["X"]\nported = "yes"\n', "`ported` must be"),
        ('[[internal]]\nmodule = "m"\nnames = ["A.b"]\n', "only allowed if ported"),
        (
            '[[internal]]\nmodule = "m"\nnames = ["X"]\nported = true\nsignatures = { X = "()" }\n',
            "need an importable entry",
        ),
        ('[[internal]]\nmodule = "m"\nnames = ["X"]\nsignature = { X = "()" }\n', "unknown key"),
        ('[[internal]]\nmodule = "m"\nnames = ["X"]\nsignatures = "()"\n', "must be a table"),
        ('[[internal]]\nmodule = "m"\nnames = ["X"]\nsignatures = { Y = "()" }\n', "not covered"),
        (
            '[[internal]]\nmodule = "m"\nnames = ["Query"]\nsignatures = { Query.get = "()" }\n',
            "must be a string - quote dotted keys",
        ),
        ('[[internal]]\nmodule = "django.db.models"\nnames = ["sql"]\n', "is a submodule"),
        ('[[attribute]]\nnames = ["_x"]\n', "`owner` must be a str"),
        ('[[attribute]]\nowner = "a.B"\nnames = "_x"\n', "`names` must be a non-empty list"),
        ('[[attribute]]\nowner = "a.B"\nnames = ["_x", "_x"]\n', "listed twice"),
        ('[[attribute]]\nowner = "a.B"\nnames = ["_x"]\nname = "y"\n', "unknown key"),
        ('[[attribute]]\nowner = "a.B"\nnames = ["_x"]\nported = true\n', "unknown key"),
        ("[[extra]]\nx = 1\n", "unknown key"),
        (
            '[[internal]]\nmodule = "m"\nnames = ["*"]\nported = true\n' + REC,
            "ported entries must name what was ported",
        ),
        ('[[internal]]\nmodule = "m"\nnames = ["X"]\n', "`used_by` must be a non-empty list"),
        (
            '[[internal]]\nmodule = "m"\nnames = ["X"]\nused_by = []\nreason = "r"\nverified_against = "1"\n',
            "`used_by` must be a non-empty list",
        ),
        (
            '[[internal]]\nmodule = "m"\nnames = ["X"]\nused_by = ["a"]\nverified_against = "1"\n',
            "`reason` must be a non-empty string",
        ),
        (
            '[[internal]]\nmodule = "m"\nnames = ["X"]\nused_by = ["a"]\nreason = "r"\n',
            "`verified_against` must be a non-empty string",
        ),
        ('[[attribute]]\nowner = "a.B"\nnames = ["_x"]\n', "`used_by` must be a non-empty list"),
        (
            '[[attribute]]\nowner = "a.B"\nnames = ["_x"]\nused_by = ["a"]\nverified_against = "1"\n',
            "`reason` must be a non-empty string",
        ),
        (
            '[[attribute]]\nowner = "a.B"\nnames = ["_x"]\nused_by = ["a"]\nreason = "r"\n',
            "`verified_against` must be a non-empty string",
        ),
    ],
    ids=[
        "internal-without-module",
        "module-not-a-string",
        "names-omitted",
        "names-as-string",
        "names-empty",
        "names-not-strings",
        "duplicate-name",
        "ported-not-a-bool",
        "dotted-name-not-ported",
        "ported-with-signatures",
        "misspelled-signatures-key",
        "signatures-not-a-table",
        "signature-not-in-names",
        "unquoted-dotted-signature",
        "submodule-as-name",
        "attribute-without-owner",
        "attribute-names-as-string",
        "attribute-duplicate-name",
        "attribute-misspelled-key",
        "attribute-with-internal-only-key",
        "unknown-top-level-table",
        "ported-wildcard",
        "internal-without-used-by",
        "internal-empty-used-by",
        "internal-without-reason",
        "internal-without-verified-against",
        "attribute-without-used-by",
        "attribute-without-reason",
        "attribute-without-verified-against",
    ],
)
def test_malformed_ledger_entries_are_rejected_with_a_clear_error(entry, message):
    # given
    text = BASE + entry
    # when / then
    with pytest.raises(ValueError, match=message):
        Ledger.from_toml(text)


@pytest.mark.parametrize(
    ("entry", "message"),
    [
        (DEP.replace('package = "asgiref"\n', ""), "`package` must be a str"),
        (DEP.replace('"asgiref"', "1"), "`package` must be a str"),
        (DEP.replace('"asgiref"', '"Asgiref"'), "lower-case name"),
        (DEP.replace('"asgiref"', '"a-b"'), "lower-case name"),
        (DEP.replace('"asgiref"', '"1abc"'), "lower-case name"),
        (DEP.replace('"asgiref"', '"django"'), "not by a \\[\\[dependency\\]\\] row"),
        (DEP.replace('"local.Local"', '""'), "never empty"),
        (DEP.replace('"local.Local"', '"*"'), "never empty"),
        (DEP.replace('"local.Local"', '"local..Local"'), "never empty"),
        (DEP.replace('"local.Local"', '".Local"'), "never empty"),
        (
            DEP.replace('names = ["local.Local", "sync.sync_to_async"]', "names = []"),
            "`names` must be",
        ),
        (DEP.replace('"sync.sync_to_async"', '"local.Local"'), "`local.Local` is listed twice"),
        (
            DEP + DEP.replace("local.Local", "x").replace(', "sync.sync_to_async"', ""),
            "`asgiref` is listed twice",
        ),
        (DEP + "ported = true\n", "unknown key"),
        (DEP + 'signatures = { x = "()" }\n', "unknown key"),
        (DEP.replace("used_by", "used"), "unknown key"),
        (
            DEP.replace('used_by = ["src/asyncopation/_django/connections.py"]\n', ""),
            "`used_by` must be a non-empty list",
        ),
        (DEP.replace('reason = "r"\n', ""), "`reason` must be a non-empty string"),
        (
            DEP.replace('verified_against = "3.13.0"\n', ""),
            "`verified_against` must be a non-empty string",
        ),
        (DEP.replace("3.13.0", "3.13"), "release version"),
        (DEP.replace("3.13.0", "3.13.0rc1"), "release version"),
        (DEP.replace("3.13.0", ">=3.13"), "release version"),
        (DEP.replace("3.13.0", "latest"), "release version"),
    ],
    ids=[
        "no-package",
        "package-not-a-string",
        "package-upper-case",
        "package-with-a-dash",
        "package-starting-with-a-digit",
        "package-django",
        "empty-name",
        "star-name",
        "empty-name-part",
        "leading-dot-name",
        "names-empty",
        "duplicate-name",
        "duplicate-package-row",
        "ported-key",
        "signatures-key",
        "misspelled-key",
        "no-used-by",
        "no-reason",
        "no-verified-against",
        "two-part-version",
        "prerelease-version",
        "specifier-as-version",
        "non-version",
    ],
)
def test_malformed_dependency_rows_are_rejected_with_a_clear_error(entry, message):
    # when / then
    with pytest.raises(ValueError, match=message):
        Ledger.from_toml(BASE + entry)


@pytest.mark.parametrize(
    ("text", "message"),
    [
        ("", "the \\[public\\] table is missing"),
        ("[public]\nmodules = [1]\n", "list of strings"),
        ("[public]\nmodules = []\nextra = 1\n", "unknown key"),
        (
            BASE + ('[[internal]]\nmodule = "django.db.models"\nnames = ["*"]\n' + REC) * 2,
            "listed twice",
        ),
    ],
    ids=["no-public-table", "modules-not-strings", "unknown-public-key", "duplicate-entry"],
)
def test_malformed_ledger_structure_is_rejected(text, message):
    # when / then
    with pytest.raises(ValueError, match=message):
        Ledger.from_toml(text)


def test_missing_django_fails_loudly_instead_of_disabling_the_submodule_check(monkeypatch):
    # given
    monkeypatch.setattr(archlint, "_django_root", lambda: None)
    text = BASE + '[[internal]]\nmodule = "django.db.models"\nnames = ["X"]\n'
    # when / then
    with pytest.raises(RuntimeError, match="uv sync"):
        Ledger.from_toml(text)
