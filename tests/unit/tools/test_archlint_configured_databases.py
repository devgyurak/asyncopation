"""Reading `connections.settings` is allowed only in `_django/connections.py` (A102, ADR-0008)."""

import pytest

from tests.support.guards import distinct_codes

P = pytest.param
EXEMPT = "src/asyncopation/_django/connections.py"
OTHER_DJANGO = "src/asyncopation/_django/other.py"
CONNECTION = "src/asyncopation/connection/registry.py"
QUERY = "src/asyncopation/query/x.py"
SNAPSHOT_DEF = "def _snapshot(value):\n    return value\n\n\n"
READER_HEAD = "def {name}():\n    from django.db import connections\n\n"


def module(body: str, name: str = "configured_databases", snapshot: str = SNAPSHOT_DEF) -> str:
    """A module with its own `_snapshot` (unless `snapshot` says otherwise) and one reader."""
    return "import copy\n\n\n" + snapshot + READER_HEAD.format(name=name) + f"    {body}\n"


@pytest.mark.parametrize(
    "source",
    [
        P(module("return _snapshot(connections.settings)"), id="own-snapshot"),
        P(module("x = _snapshot(connections.settings)\n    return x"), id="bound-first"),
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
        P(module("return connections.settings"), id="returned-as-is"),
        P(module("return dict(connections.settings)"), id="shallow-copy"),
        P(module("return copy.deepcopy(connections.settings)"), id="deepcopy-no-longer-allowed"),
        P(module("return _snapshot(connections.databases)"), id="other-attribute"),
        P(module("return copy.copy(connections.settings)"), id="not-the-snapshot"),
        P(module("connections.settings = {}"), id="assigned"),
        P(module("return _snapshot(connections)"), id="whole-handler"),
        P(module("return connections['default']"), id="alias-lookup"),
        P(module("return _snapshot(connections.settings)", name="other_reader"), id="other-fn"),
    ],
)
def test_other_uses_of_connections_in_the_exempt_module_are_reported(source):
    # when
    codes = distinct_codes(source, EXEMPT)
    # then
    assert codes == ["A102"]


@pytest.mark.parametrize(
    "source",
    [
        P(module("return _snapshot(connections.settings)", snapshot=""), id="no-own-def"),
        P(
            module(
                "return _snapshot(connections.settings)",
                snapshot="from asyncopation.compile.postgresql import _snapshot\n\n\n",
            ),
            id="imported-from-another-module",
        ),
        P(
            module(
                "return _snapshot(connections.settings)",
                snapshot=SNAPSHOT_DEF + "_snapshot = dict\n\n\n",
            ),
            id="rebound-after-the-def",
        ),
        P(
            module(
                "return _snapshot(connections.settings)",
                snapshot=SNAPSHOT_DEF + "from copy import deepcopy as _snapshot\n\n\n",
            ),
            id="re-imported-after-the-def",
        ),
    ],
)
def test_a_snapshot_that_is_not_the_modules_own_def_is_reported(source):
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
    source = module("return _snapshot(connections.settings)")
    # when
    codes = distinct_codes(source, path)
    # then
    assert "A102" in codes
