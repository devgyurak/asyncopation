"""Reading `connections.settings` is allowed only in `_django/connections.py` (A102, ADR-0008)."""

import pytest

from tests.support.guards import distinct_codes, reader_with, real_connections_module

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


R = "return _snapshot(connections.settings)\n"
LIFECYCLE = "src/asyncopation/connection/lifecycle.py"
IMPORT = "from asyncopation._django.connections import configured_databases\n"
LIFE_IMPORT = "from asyncopation.connection.lifecycle import configured_databases\n"
LIFE_USE = "\n\ndef f():\n    return lifecycle.configured_databases()\n"


@pytest.mark.parametrize(
    ("source", "path"),
    [
        P(IMPORT, LIFECYCLE, id="lc-ok"),
        P(IMPORT + "\n\ndef f():\n    return configured_databases()\n", LIFECYCLE, id="lc-used"),
    ],
)
def test_the_one_plain_import_in_lifecycle_is_clean(source, path):
    # when
    codes = distinct_codes(source, path)
    # then
    assert codes == []


def test_the_real_connections_module_is_clean():
    # given
    source = real_connections_module()
    # when
    codes = distinct_codes(source, EXEMPT)
    # then
    assert codes == []


@pytest.mark.parametrize(
    ("source", "path"),
    [
        P(IMPORT + "__all__ = ['configured_databases']\n", LIFECYCLE, id="lc-reexport-all"),
        P(LIFE_IMPORT, QUERY, id="q-one-hop-from"),
        P("from asyncopation.connection import lifecycle\n" + LIFE_USE, QUERY, id="q-one-hop-attr"),
        P(
            "from asyncopation.connection import lifecycle\n\n\ndef f():\n"
            "    return getattr(lifecycle, 'configured_databases')()\n",
            QUERY,
            id="q-one-hop-getattr",
        ),
        P(LIFE_IMPORT, CONNECTION, id="reg-from-lifecycle"),
        P("from asyncopation.connection.lifecycle import *\n", QUERY, id="star-from-lifecycle"),
        P(
            "def f():\n    return __import__('asyncopation._django.connections', fromlist=['x'])"
            ".swapped\n",
            LIFECYCLE,
            id="lc-dunder-import",
        ),
        P("from .._django.connections import configured_databases\n", LIFECYCLE, id="lc-relative"),
        P(
            "from .._django.connections import configured_databases, swapped\n",
            LIFECYCLE,
            id="lc-relative-swapped",
        ),
        P(
            "import sys\n\n\ndef f():\n    return sys.modules['asyncopation._django.connections']"
            ".swapped\n",
            LIFECYCLE,
            id="lc-sys-modules",
        ),
        P(
            "from asyncopation._django.connections import configured_databases as swapped\n",
            LIFECYCLE,
            id="lc-reader-alias-swapped",
        ),
        P(
            IMPORT + "\n\ndef f():\n    return configured_databases.__globals__['swapped']\n",
            LIFECYCLE,
            id="lc-reader-dunder-globals",
        ),
        P(
            "import importlib\n\n\ndef f():\n    return importlib.import_module("
            "'asyncopation._django.connections').swapped\n",
            LIFECYCLE,
            id="lc-importlib",
        ),
    ],
)
def test_the_reader_name_outside_the_two_exemptions_is_reported(source, path):
    # when
    codes = distinct_codes(source, path)
    # then
    assert "A102" in codes


@pytest.mark.parametrize(
    "source",
    [
        P(
            SNAPSHOT_DEF.replace(
                "return value",
                "from django.db import connections as c\n    return c['default'].cursor()",
            )
            + reader_with(R),
            id="snapshot-alias-conn",
        ),
        P(
            SNAPSHOT_DEF + reader_with(R) + "\n\ndef other():\n    import django.db\n"
            "    return django.db.connections['default']\n",
            id="handler-via-other-fn",
        ),
        P(
            SNAPSHOT_DEF + reader_with("def inner():\n        return connections['default']", R),
            id="nested-def",
        ),
        P(SNAPSHOT_DEF + reader_with("g = lambda: connections['default']", R), id="nested-lambda"),
        P(SNAPSHOT_DEF + "async " + reader_with(R), id="async-reader"),
        P(
            SNAPSHOT_DEF + "class K:\n    " + reader_with(R).replace("\n", "\n    "),
            id="method-reader",
        ),
        P(
            SNAPSHOT_DEF + reader_with("global _snapshot", R) + "\n\ndef patch():\n"
            "    global _snapshot\n    _snapshot = lambda v: v\n",
            id="global-rebind",
        ),
        P(
            SNAPSHOT_DEF + "globals()['_snapshot'] = dict\n\n\n" + reader_with(R),
            id="globals-trick",
        ),
        P(SNAPSHOT_DEF + "del _snapshot\n\n\n" + reader_with(R), id="snapshot-del"),
        P(SNAPSHOT_DEF + SNAPSHOT_DEF + reader_with(R), id="snapshot-redef"),
        P(SNAPSHOT_DEF + reader_with(R).replace("()", "(_snapshot=dict)"), id="snapshot-param"),
        P(SNAPSHOT_DEF + reader_with("_snapshot = lambda v: v", R), id="snapshot-local"),
        P(
            SNAPSHOT_DEF
            + reader_with("return (_snapshot := id) and _snapshot(connections.settings)"),
            id="snapshot-walrus",
        ),
        P(
            SNAPSHOT_DEF + reader_with("for _snapshot in [dict]:\n        pass", R),
            id="snapshot-for",
        ),
        P(
            SNAPSHOT_DEF
            + reader_with(
                "import contextlib\n    with contextlib.nullcontext(dict) "
                "as _snapshot:\n        " + R
            ),
            id="snapshot-with",
        ),
        P(
            "import functools\n\n@functools.cache\n" + SNAPSHOT_DEF + reader_with(R),
            id="snapshot-decorated",
        ),
        P(
            "@lambda f: (lambda v: v)\n" + SNAPSHOT_DEF + reader_with(R),
            id="snapshot-decorator-lambda",
        ),
        P(SNAPSHOT_DEF + reader_with(R).replace("()", "(x=None)"), id="reader-default-arg"),
        P(
            SNAPSHOT_DEF + reader_with(R).replace("()", "(_snapshot=lambda v: v)"),
            id="reader-param",
        ),
        P(SNAPSHOT_DEF + "_snapshot = x = (lambda v: v)\n\n" + reader_with(R), id="multi-target"),
        P(SNAPSHOT_DEF + "_snapshot, y = (lambda v: v), 1\n\n" + reader_with(R), id="tuple-unpack"),
    ],
)
def test_bypasses_of_the_pinned_snapshot_and_reader_are_reported(source):
    # when
    codes = distinct_codes(source, EXEMPT)
    # then
    assert "A102" in codes
