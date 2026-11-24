import pytest

from tools.guards.archlint import Ledger

BASE = '[public]\nmodules = ["django.db"]\n'


def test_internal_signatures_ported_and_attribute_keys_are_parsed():
    # given
    text = (
        BASE
        + """
[[internal]]
module = "django.db.models.sql.query"
names = ["Query"]
signatures = { "Query.get_compiler" = "(self, using=None)" }
ported = true

[[attribute]]
owner = "django.db.models.query.QuerySet"
names = ["_fetch_mode"]
signatures = { _fetch_mode = "()" }
"""
    )
    # when
    ledger = Ledger.from_toml(text)
    # then
    module = "django.db.models.sql.query"
    assert ledger.internal == {(module, "Query")}
    assert ledger.signatures == {(module, "Query.get_compiler", "(self, using=None)")}
    assert ledger.ported == {(module, "Query")}
    assert ledger.attributes == {("django.db.models.query.QuerySet", "_fetch_mode")}
    assert ledger.attribute_signatures == {("django.db.models.query.QuerySet", "_fetch_mode", "()")}


@pytest.mark.parametrize(
    ("entry", "message"),
    [
        ('[[internal]]\nnames = ["X"]\n', "`module` must be a str"),
        ('[[internal]]\nmodule = "m"\nnames = "X"\n', "`names` must be a non-empty list"),
        (
            '[[internal]]\nmodule = "m"\nnames = ["X"]\nsignatures = { Y = "()" }\n',
            "not covered by `names`",
        ),
        (
            '[[internal]]\nmodule = "m"\nnames = ["Query"]\nsignatures = { Query.get = "()" }\n',
            "must be a string - quote dotted keys",
        ),
        ('[[internal]]\nmodule = "django.db.models"\nnames = ["sql"]\n', "is a submodule"),
        ('[[attribute]]\nnames = ["_x"]\n', "`owner` must be a str"),
        ('[[attribute]]\nowner = "a.B"\nnames = "_x"\n', "`names` must be a non-empty list"),
    ],
    ids=[
        "internal-without-module",
        "names-as-string",
        "signature-not-in-names",
        "unquoted-dotted-signature",
        "submodule-as-name",
        "attribute-without-owner",
        "attribute-names-as-string",
    ],
)
def test_malformed_ledger_entries_are_rejected_with_a_clear_error(entry, message):
    # when / then
    with pytest.raises(ValueError, match=message):
        Ledger.from_toml(BASE + entry)
