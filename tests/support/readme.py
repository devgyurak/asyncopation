"""Run the python snippets of a README section (documentation that cannot rot)."""

from __future__ import annotations

import ast
import asyncio
import inspect
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

README = Path(__file__).resolve().parents[2] / "README.md"
_FENCE = re.compile(r"```python\n(.*?)```", re.DOTALL)


def section_snippets(heading: str, readme: Path = README) -> list[str]:
    """The ```python blocks between `## <heading>` and the next `## ` heading."""
    text = readme.read_text(encoding="utf-8")
    start = re.search(rf"^## {re.escape(heading)}\s*$", text, re.MULTILINE)
    if start is None:
        return []
    rest = text[start.end() :]
    end = re.search(r"^## ", rest, re.MULTILINE)
    body = rest[: end.start()] if end else rest
    return _FENCE.findall(body)


@dataclass(frozen=True)
class SnippetRun:
    error: BaseException | None


def run_snippets(snippets: list[str]) -> SnippetRun:
    """Execute each snippet (top-level `await` allowed) with `asyncio.run`; stop at the first error."""
    for snippet in snippets:
        code = compile(snippet, "<readme>", "exec", flags=ast.PyCF_ALLOW_TOP_LEVEL_AWAIT)
        try:
            result: Any = eval(code, {"__name__": "__readme__"})
            if inspect.isawaitable(result):
                asyncio.run(result)
        except Exception as exc:
            return SnippetRun(exc)
    return SnippetRun(None)
