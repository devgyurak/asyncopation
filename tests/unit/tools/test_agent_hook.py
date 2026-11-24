import pytest

from tools.hooks.agent_hook import extract_paths, pre

CODEX_PATCH = """*** Begin Patch
*** Update File: src/asyncopation/query/__init__.py
@@
-a
+b
*** Add File: tests/unit/test_new.py
+x
*** End Patch"""


@pytest.mark.parametrize(
    ("tool_input", "expected"),
    [
        ({"file_path": "/repo/a.py", "content": "x"}, ["/repo/a.py"]),
        ({"path": "b.py"}, ["b.py"]),
        (
            {"command": CODEX_PATCH},
            ["src/asyncopation/query/__init__.py", "tests/unit/test_new.py"],
        ),
        ({"command": "ls -la"}, []),
    ],
    ids=["claude-edit", "pi-write", "codex-apply-patch", "shell"],
)
def test_paths_are_extracted_from_every_agent_payload(tool_input, expected):
    # when
    paths = extract_paths(tool_input)
    # then
    assert paths == expected


@pytest.mark.parametrize(
    ("path", "expected_exit"),
    [
        (".claude/agents/architect.md", 2),
        (".codex/agents/x.toml", 2),
        (".agents/agents/architect.md", 0),
    ],
)
def test_pre_hook_blocks_only_generated_agent_files(path, expected_exit):
    # when
    exit_code = pre([path])
    # then
    assert exit_code == expected_exit
