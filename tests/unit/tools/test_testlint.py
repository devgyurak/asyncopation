import pytest

from tests.support.guards import lint_test_module
from tools.guards.testlint import applies_to

GOOD = """
def test_ok():
    # given
    value = 1
    # when
    result = value + 1
    # then
    assert result == 2
"""

WHEN_THEN_COMBINED = """
import pytest

def test_raises():
    # given
    value = {}
    # when / then
    with pytest.raises(KeyError):
        value["missing"]
"""


@pytest.mark.parametrize("source", [GOOD, WHEN_THEN_COMBINED], ids=["gwt", "when-then"])
def test_well_formed_test_has_no_violations(source):
    # when
    codes = lint_test_module(source)
    # then
    assert codes == []


@pytest.mark.parametrize(
    "statement",
    [
        "for x in []: pass",
        "while False: pass",
        "if True: pass",
        "y = 1 if True else 2",
        "y = [x for x in []]",
        "y = {x: x for x in []}",
        "y = sum(x for x in [])",
        "try:\n        pass\n    except Exception:\n        pass",
        "match 1:\n        case 1: pass",
    ],
    ids=["for", "while", "if", "ternary", "listcomp", "dictcomp", "genexp", "try", "match"],
)
def test_control_flow_in_test_module_is_reported(statement):
    # given
    source = f"""
def test_x():
    # when
    {statement}
    # then
    assert True
"""
    # when
    codes = lint_test_module(source)
    # then
    assert "T101" in codes


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ("def test_x():\n    # given\n    a = 1\n    # then\n    assert a\n", ["T201"]),
        ("def test_x():\n    # when\n    a = 1\n    assert a\n", ["T202"]),
        (
            "def test_x():\n    # when\n    a = 1\n    # given\n    b = 2\n    # then\n    assert a\n",
            ["T203"],
        ),
        (
            "def test_x():\n    # when\n    a = 1\n    # when\n    b = 2\n    # then\n    assert a\n",
            ["T204"],
        ),
        ("def test_x():\n    a = 1\n    # when\n    b = a\n    # then\n    assert b\n", ["T205"]),
        ("def test_x():\n    # when\n    a = 1\n    # then\n    print(a)\n", ["T206"]),
        (
            "import time\ndef test_x():\n    # when\n    time.sleep(1)\n    # then\n    assert True\n",
            ["T301"],
        ),
    ],
    ids=["no-when", "no-then", "order", "duplicate", "code-before-when", "no-assert", "sleep"],
)
def test_gwt_problems_are_reported(source, expected):
    # when
    codes = lint_test_module(source)
    # then
    assert codes == expected


def test_given_marker_above_an_explanatory_comment_block_counts():
    # given
    source = (
        "def test_x():\n    # given: a value\n    # (explained over two lines)\n    a = 1\n"
        "    # when\n    b = a\n    # then\n    assert b\n"
    )
    # when
    codes = lint_test_module(source)
    # then
    assert codes == []


def test_given_marker_separated_from_the_body_by_a_blank_line_is_not_in_the_function():
    # given
    source = (
        "def test_x():\n    # given\n\n    a = 1\n    # when\n    b = a\n    # then\n    assert b\n"
    )
    # when
    codes = lint_test_module(source)
    # then
    assert codes == ["T205"]


def test_a_marker_above_the_def_line_is_not_in_the_function_even_with_a_trailing_comment():
    # given
    source = (
        "# given\ndef test_x():  # a trailing note\n    a = 1\n    # when\n    b = a\n"
        "    # then\n    assert b\n"
    )
    # when
    codes = lint_test_module(source)
    # then
    assert codes == ["T205"]


def test_a_marker_trailing_the_def_line_is_not_in_the_function():
    # given
    source = "def test_x():  # given\n    a = 1\n    # when\n    b = a\n    # then\n    assert b\n"
    # when
    codes = lint_test_module(source)
    # then
    assert codes == ["T205"]


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        ("tests/unit/test_query.py", True),
        ("tests/integration/sub/test_x.py", True),
        ("tests/unit/support/test_strategies.py", True),
        ("tests/support/differential.py", False),
        ("tests/support/sub/test_helpers.py", False),
        ("tests/conftest.py", False),
        ("tools/guards/testlint.py", False),
    ],
)
def test_only_test_modules_are_checked(path, expected):
    # when
    result = applies_to(path)
    # then
    assert result is expected
