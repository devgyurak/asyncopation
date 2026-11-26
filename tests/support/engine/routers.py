"""A database router that records and scripts what it answers (settings override in the engine tests)."""

from __future__ import annotations

from typing import Any, ClassVar

PATH = "tests.support.engine.routers.ScriptedRouter"


class ScriptedRouter:
    """Answers `db_for_read` (and `db_for_write`) from `answers` in order (the last answer repeats) and counts the asks."""

    reads: ClassVar[list[str]] = []
    answers: ClassVar[list[str]] = ["default"]

    def db_for_read(self, model: Any, **hints: Any) -> str:
        ScriptedRouter.reads.append(model._meta.label)
        index = min(len(ScriptedRouter.reads), len(ScriptedRouter.answers)) - 1
        return ScriptedRouter.answers[index]

    def db_for_write(self, model: Any, **hints: Any) -> str:
        """`select_for_update()` marks a QuerySet `_for_write`: its `db` asks for a write alias."""
        return self.db_for_read(model, **hints)


def script(answers: list[str]) -> None:
    ScriptedRouter.reads = []
    ScriptedRouter.answers = answers
