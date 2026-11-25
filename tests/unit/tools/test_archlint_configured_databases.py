"""Reading `connections.settings` is allowed only in `_django/connections.py` (A102, ADR-0008)."""

import pytest

from tests.support.guards import distinct_codes

P = pytest.param
EXEMPT = "src/asyncopation/_django/connections.py"
OTHER_DJANGO = "src/asyncopation/_django/other.py"
CONNECTION = "src/asyncopation/connection/registry.py"
QUERY = "src/asyncopation/query/x.py"
HEAD = "import copy\n\n\ndef configured_databases():\n    from django.db import connections\n\n"


def reader(body: str, name: str = "configured_databases") -> str:
    return HEAD.replace("configured_databases", name) + f"    {body}\n"


@pytest.mark.parametrize(
    "source",
    [
        P(reader("return copy.deepcopy(connections.settings)"), id="copy-deepcopy"),
        P(
            "from copy import deepcopy\n\n\ndef configured_databases():\n"
            "    from django.db import connections\n\n"
            "    return deepcopy(connections.settings)\n",
            id="imported-deepcopy",
        ),
    ],
)
def test_the_reader_in_the_exempt_module_is_clean(source):
    # when
    codes = distinct_codes(source, EXEMPT)
    # then
    assert codes == []


@pytest.mark.parametrize(
    "source",
    [
        P(reader("return connections.settings"), id="returned-as-is"),
        P(reader("return dict(connections.settings)"), id="shallow-copy"),
        P(reader("return copy.deepcopy(connections.databases)"), id="other-attribute"),
        P(reader("return copy.copy(connections.settings)"), id="not-deepcopy"),
        P(reader("connections.settings = {}"), id="assigned"),
        P(reader("return copy.deepcopy(connections)"), id="whole-handler"),
        P(reader("return connections['default']"), id="alias-lookup"),
        P(reader("return copy.deepcopy(connections.settings)", name="other_reader"), id="other-fn"),
    ],
)
def test_other_uses_of_connections_in_the_exempt_module_are_reported(source):
    # when
    codes = distinct_codes(source, EXEMPT)
    # then
    assert codes == ["A102"]


@pytest.mark.parametrize(
    "path",
    [
        P(OTHER_DJANGO, id="another-django-adapter-module"),
        P(CONNECTION, id="connection-layer"),
        P(QUERY, id="query-layer"),
    ],
)
def test_reading_connections_settings_elsewhere_is_reported(path):
    # given
    source = reader("return copy.deepcopy(connections.settings)")
    # when
    codes = distinct_codes(source, path)
    # then
    assert "A102" in codes
