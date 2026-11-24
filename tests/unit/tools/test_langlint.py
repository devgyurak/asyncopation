import pytest

from tests.support.guards import lint_language
from tools.guards.langlint import translation_of


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ("x = 1  # an English comment\n", []),
        ("x = 1  # 한국어 주석\n", ["L101"]),
        ('"""모듈 설명."""\n', ["L101"]),
        ('def f():\n    """함수 설명."""\n', ["L101"]),
        ('label = "음수 ✓"\n', []),
    ],
    ids=[
        "english-comment",
        "korean-comment",
        "korean-module-docstring",
        "korean-function-docstring",
        "korean-string-literal-is-data",
    ],
)
def test_python_comments_and_docstrings_must_be_english(source, expected):
    # when
    codes = lint_language(source)
    # then
    assert codes == expected


def test_non_english_markdown_outside_translations_is_reported():
    # given
    text = "# Title\n\n설명\n"
    # when
    codes = lint_language(text, path="docs/notes.md")
    # then
    assert codes == ["L102"]


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        ("README-KO.md", ("KO", "README.md")),
        ("docs/design/plan-KO.md", ("KO", "docs/design/plan.md")),
        ("docs/plan-V2.md", None),
        ("docs/adr/0001-connection-ownership.md", None),
    ],
)
def test_translation_files_are_recognised_by_suffix(path, expected):
    # when
    result = translation_of(path, {"KO"})
    # then
    assert result == expected
