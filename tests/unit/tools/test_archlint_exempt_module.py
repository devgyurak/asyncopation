"""The one module allowed to touch `connections` (ADR-0008, Decision 6): shape and rules."""

import pytest

from tests.support.guards import distinct_codes, in_function, rule_outcome, with_peek

P = pytest.param
PATH = "src/asyncopation/_django/connections.py"
PEEK_CALL = "getattr(connections._connections, alias, _ABSENT)"
PEEK = f"p = {PEEK_CALL}"
SHAPE = "only `connections[alias] = ...`"
TRACKED = "the peeked wrapper may only be compared"
LOCAL = "must stay a local variable"
IMPORT = "must not be used"


@pytest.mark.parametrize(
    "source",
    [
        P(in_function(f"{PEEK}\nconnections[alias] = wrapper"), id="setitem"),
        P(in_function(f"{PEEK}\ndel connections[alias]"), id="delitem"),
        P(in_function(f"previous = {PEEK_CALL}"), id="peek-assign"),
        P(in_function(f"previous: object = {PEEK_CALL}"), id="peek-annassign"),
        P(with_peek("connections[alias] = x"), id="store-back"),
        P(
            with_peek("if x is None:\n    del connections[alias]\nelse:\n    connections[alias] = x"),
            id="identity-then-restore",
        ),
        P(with_peek("if x is not None:\n    connections[alias] = x"), id="is-not"),
        P(
            in_function("p = getattr(connections._connections, name, None)\nconnections[name] = p")
            .replace("swapped(alias, wrapper)", "swapped(name, wrapper)"),
            id="key-named-name",
        ),
    ],
)  # fmt: skip
def test_the_swap_shape_of_the_real_module_is_clean(source):
    # when
    codes = distinct_codes(source, PATH)
    # then
    assert codes == []


@pytest.mark.parametrize(
    ("source", "rule"),
    [
        P("from django.db import connections\n", IMPORT, id="module-level-import"),
        P("if True:\n    from django.db import connections\n", IMPORT, id="inside-if"),
        P(
            "try:\n    from django.db import connections\nexcept ImportError:\n    pass\n",
            IMPORT,
            id="inside-try",
        ),
        P("class C:\n    from django.db import connections\n", IMPORT, id="inside-class"),
        P("def f():\n    from django.db import connections as c\n", IMPORT, id="import-alias"),
        P("from django.db import connection\n", IMPORT, id="connection"),
        P("from django.db import transaction\n", IMPORT, id="transaction"),
        P("from django.db import close_old_connections\n", IMPORT, id="close-old-connections"),
        P("from django.db import reset_queries\n", IMPORT, id="reset-queries"),
        P("from django.db import ConnectionHandler\n", IMPORT, id="connection-handler"),
        P("from django.db import load_backend\n", IMPORT, id="load-backend"),
    ],
)
def test_binding_the_django_handler_outside_a_function_or_under_another_name_is_reported(
    source, rule
):
    # when
    outcome = rule_outcome(source, PATH, "A102", rule)
    # then
    assert outcome == (["A102"], True)


@pytest.mark.parametrize(
    ("source", "rule"),
    [
        P(in_function("return connections[alias]"), SHAPE, id="load"),
        P(in_function("connections.close_all()"), SHAPE, id="close-all"),
        P(in_function("return connections._connections.default"), SHAPE, id="storage-chain"),
        P(in_function("return connections._connections"), SHAPE, id="bare-storage"),
        P(in_function(f"return {PEEK_CALL}.cursor()"), "`.cursor` is a sync connection", id="cursor"),
        P(in_function(f"return {PEEK_CALL}"), SHAPE, id="peek-returned"),
        P(in_function(PEEK_CALL), SHAPE, id="peek-expression-statement"),
        P(in_function("c = connections"), SHAPE, id="alias-assignment"),
        P(in_function("g(connections)"), SHAPE, id="passed-as-argument"),
        P(in_function("x = getattr(connections.settings, alias, None)"), SHAPE, id="settings-peek"),
        P(in_function(f"a = b = {PEEK_CALL}"), SHAPE, id="chained-assignment"),
        P(in_function(f"wrapper.p = {PEEK_CALL}"), SHAPE, id="attribute-target"),
        P(in_function(f"yield {PEEK_CALL}"), SHAPE, id="yielded-peek"),
        P(in_function(f"{PEEK}\ndel connections[other]"), SHAPE, id="delete-under-another-name"),
        P(in_function(f"{PEEK}\nconnections[alias] += 1"), SHAPE, id="augmented-store-of-a-constant"),
        P(in_function("connections[alias] += wrapper"), SHAPE, id="augmented-store-of-argument"),
        P(in_function(f"{PEEK}\nconnections[alias]"), SHAPE, id="bare-load-statement"),
        P(in_function(f"{PEEK}\ndel connections['other']"), SHAPE, id="delete-with-a-constant-key"),
        P(in_function(f"{PEEK}\nconnections['other'] = wrapper"), SHAPE,
          id="store-with-a-constant-key"),
    ],
)  # fmt: skip
def test_anything_beyond_the_swap_shape_is_reported(source, rule):
    # when
    outcome = rule_outcome(source, PATH, "A102", rule)
    # then
    assert outcome == (["A102"], True)


@pytest.mark.parametrize(
    ("source", "rule"),
    [
        P(with_peek("getattr(x, 'cursor')"), TRACKED, id="getattr-on-peeked"),
        P(with_peek("g(x)"), TRACKED, id="passed-as-argument"),
        P(with_peek("return x"), TRACKED, id="returned-name"),
        P(with_peek("return x == 1"), TRACKED, id="equality-comparison"),
        P(with_peek("return x is None == wrapper"), TRACKED, id="chained-comparison"),
        P(with_peek("connections[alias] += x"), TRACKED, id="augmented-store"),
        P(with_peek("connections[alias] = wrapper.p = x"), TRACKED, id="second-assignment-target"),
        P(with_peek("connections[other] = x"), TRACKED, id="store-under-another-name"),
        P(with_peek("connections['other'] = x"), TRACKED, id="store-under-a-constant-key"),
        P(in_function("p = getattr(connections._connections, 'x', _ABSENT)\nconnections[alias] = p"),
          TRACKED, id="peek-with-a-constant-key"),
        P(
            in_function(
                f"{PEEK}\nq = getattr(connections._connections, other, None)\nconnections[other] = p"
            ),
            TRACKED,
            id="second-peek-with-another-key",
        ),
        P(in_function(f"{PEEK}\nalias = other\nconnections[alias] = p"), "must not be reassigned",
          id="key-reassigned"),
        P(in_function(f"{PEEK}\ndel alias\nconnections[alias] = p"), "must not be reassigned",
          id="key-deleted"),
        P(in_function(f"nonlocal x\nx = {PEEK_CALL}"), LOCAL, id="nonlocal-target"),
        P(in_function(f"global x\nx = {PEEK_CALL}"), LOCAL, id="global-target"),
    ],
)  # fmt: skip
def test_the_peeked_value_and_the_swap_key_are_tracked(source, rule):
    # when
    outcome = rule_outcome(source, PATH, "A102", rule)
    # then
    assert outcome == (["A102"], True)


@pytest.mark.parametrize(
    ("source", "rule"),
    [
        P(
            in_function(PEEK).replace("    @contextmanager\n", ""),
            "`swapped` uses `connections`",
            id="undecorated-swapped",
        ),
        P(
            in_function(PEEK).replace("def swapped(", "def other("),
            "`other` uses `connections`",
            id="decorated-other-function",
        ),
        P(
            in_function("pass").replace(
                "        yield\n",
                "        yield\n\n    def setter():\n        return connections\n",
            ),
            "`setter` uses `connections`",
            id="nested-setter",
        ),
        P(
            in_function("pass").replace("    def swapped", "    async def swapped"),
            "synchronous",
            id="async-swapped",
        ),
    ],
)
def test_only_the_decorated_swapped_function_may_use_connections(source, rule):
    # when
    outcome = rule_outcome(source, PATH, "A102", rule)
    # then
    assert outcome == (["A102"], True)


@pytest.mark.parametrize(
    "attribute",
    [
        "cursor", "chunked_cursor", "create_cursor", "ensure_connection", "connect", "_cursor",
        "get_new_connection", "temporary_connection", "close", "close_if_unusable_or_obsolete",
        "commit", "_commit", "rollback", "_rollback", "savepoint", "set_autocommit", "connection",
        "_getframe", "currentframe", "f_locals", "f_globals",
    ],
)  # fmt: skip
def test_operation_and_frame_attribute_names_are_banned(attribute):
    # given
    source = f"def f(w):\n    return w.{attribute}\n"
    # when
    outcome = rule_outcome(source, PATH, "A102", f"`.{attribute}`")
    # then
    assert outcome == (["A102"], True)


@pytest.mark.parametrize("name", ["exec", "eval", "locals", "vars", "globals", "dir"])
def test_introspection_names_are_banned(name):
    # when
    codes = distinct_codes(in_function(f"return {name}"), PATH)
    # then
    assert codes == ["A102"]
