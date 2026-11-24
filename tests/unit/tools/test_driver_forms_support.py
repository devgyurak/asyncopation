"""The generator behind the multi-form driver tests builds exactly these sources."""

from tests.support.guards import driver_forms, same_verdict


def test_driver_forms_builds_the_import_attribute_getattr_and_chain_sources():
    # when
    forms = driver_forms("django.db.backends.postgresql.base", "sql")
    # then
    assert forms == {
        "import": "from django.db.backends.postgresql.base import sql\n",
        "attribute": (
            "import django.db.backends.postgresql.base as m\n\ndef f():\n    return m.sql\n"
        ),
        "getattr": (
            "import django.db.backends.postgresql.base as m\n\n"
            "def f():\n    return getattr(m, 'sql')\n"
        ),
        "getattr-chain": (
            "import django.db.backends.postgresql as m\n\n"
            "def f():\n    return getattr(m, 'base').sql\n"
        ),
    }


def test_same_verdict_names_every_form():
    # when
    verdict = same_verdict(True)
    # then
    assert verdict == {"import": True, "attribute": True, "getattr": True, "getattr-chain": True}
