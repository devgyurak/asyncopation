"""Collect the django-drift tests under a marker expression, as the django-main canary does.

Also reads the canary's drift step out of ``.github/workflows/ci.yml``.
"""

from __future__ import annotations

import os
import re
import shlex
import subprocess
import sys
from functools import cache
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
DRIFT_DIR = "tests/unit/django_drift"
# Must match the `django_pin` tests in tests/unit/django_drift/test_ledger_drift.py.
LEDGER_MODULE = f"{DRIFT_DIR}/test_ledger_drift.py"
PIN_TESTS = frozenset(
    {
        f"{LEDGER_MODULE}::test_installed_django_satisfies_the_pyproject_pin",
        f"{LEDGER_MODULE}::test_django_classifiers_match_the_pyproject_pin",
    }
)
ASGIREF_VERSION_TEST = (
    f"{LEDGER_MODULE}::test_installed_dependency_is_the_version_the_ledger_was_verified_against"
)
CANARY_JOB = "django-main"
CI_WORKFLOW = ROOT / ".github" / "workflows" / "ci.yml"


_COMMENT = re.compile(r"(^|\s)#.*")
# A lone ``&`` is a separator; the ``&`` of ``2>&1`` and ``&>`` is a redirection.
_SEPARATORS = re.compile(r"(&&|\|\||\|&|[;|]|(?<!>)&(?!>))")
_SEPARATOR_TOKENS = frozenset({"&&", "||", "|&", ";", "|", "&"})
# A redirection operator on its own (``>``, ``>>``, ``2>``, ``&>``, ``>&``, ``<``): the next token is
# a file, not an argument.
_REDIRECTION = re.compile(r"^(\d*|&)?[<>]{1,2}&?$")


def _strip_comment(line: str) -> str:
    """The line without a trailing comment: a ``#`` that starts a word outside quotes cuts the rest.

    Known limit: a line with an unterminated quote (an apostrophe in an ``echo``) is not scanned
    for quotes, so a quoted `` #`` on that same line is taken as a comment.
    """
    quote = ""
    escaped = False
    for index, char in enumerate(line):
        if escaped:
            escaped = False
        elif char == "\\" and quote != "'":
            escaped = True
        elif quote:
            quote = "" if char == quote else quote
        elif char in "'\"":
            quote = char
        elif char == "#" and (index == 0 or line[index - 1].isspace()):
            return line[:index]
    return _COMMENT.sub("", line) if quote else line


def _logical_lines(command: str) -> list[str]:
    """The lines of a command with backslash continuations joined and comments cut.

    A comment ends at its newline: a backslash at the end of a comment does not continue it. The
    comment cut sees the joined line, so ``#`` right after a continuation is part of a word.
    """
    lines: list[str] = []
    carry = ""
    for raw in command.splitlines():
        line = _strip_comment(carry + raw)
        trailing = len(line) - len(line.rstrip("\\"))
        if trailing % 2 == 1:
            carry = line[:-1]
            continue
        lines.append(line)
        carry = ""
    return [*lines, carry] if carry else lines


def _line_tokens(line: str) -> list[str]:
    try:
        lexer = shlex.shlex(line, posix=True, punctuation_chars=True)
        lexer.whitespace_split = True
        lexer.commenters = ""
        return list(lexer)
    except ValueError:
        return _SEPARATORS.sub(r" \1 ", line).split()


def _tokens(command: str) -> list[str]:
    """Shell-like tokens of a command, with ``&&``, ``;``, ``|`` ... and a newline as tokens of
    their own (a newline ends a command like ``;`` does).

    A ``#`` that starts a word and is not quoted cuts the rest of its line, on both paths (a line
    with unbalanced quotes falls back to a plain split), so a commented-out ``pytest`` line is never
    a drift step, ``-k 'a #b'`` keeps its argument and ``a#b`` is one word.
    """
    tokens: list[str] = []
    for line in _logical_lines(command):
        tokens += [*_line_tokens(line), ";"]
    return tokens


def _is_drift_folder(token: str) -> bool:
    return token.rstrip("/").removeprefix("./") == DRIFT_DIR


def _arguments(segment: list[str]) -> list[str]:
    """The tokens after ``pytest`` in a segment, without redirection operators and their files."""
    after = segment[segment.index("pytest") + 1 :]
    return [
        token
        for index, token in enumerate(after)
        if not _REDIRECTION.match(token) and not (index and _REDIRECTION.match(after[index - 1]))
    ]


def _pytest_segments(tokens: list[str]) -> list[list[str]]:
    """The arguments after ``pytest`` in each command segment that runs it."""
    segments: list[list[str]] = [[]]
    for token in tokens:
        if token in _SEPARATOR_TOKENS:
            segments.append([])
        else:
            segments[-1].append(token)
    return [_arguments(segment) for segment in segments if "pytest" in segment]


def runs_drift_folder(command: str) -> bool:
    """Whether a shell command runs ``pytest`` with the drift folder as one of its arguments.

    Token based and scoped to the command segment (split on ``&&``, ``||``, ``;``, ``|``, ``|&``,
    ``&`` and a newline) that contains a ``pytest`` token: options between ``pytest`` and the path
    (``pytest -q tests/unit/django_drift``), a trailing slash and a ``./`` prefix match, while a
    longer folder name (``..._x``), a path outside the repository (``../...``, ``/abs/...``), a
    comment, a redirection target and a path in another segment do not.

    Known limits, kept simple on purpose (each is pinned by a ``known-limit`` test): lines are
    processed one at a time, so text inside a multi-line quoted string or a heredoc is read as
    commands; any ``pytest`` token counts, so ``echo pytest <folder>`` matches; a quoted ``#`` on a
    line with an unterminated quote is read as a comment.
    """
    return any(
        any(_is_drift_folder(token) for token in arguments)
        for arguments in _pytest_segments(_tokens(command))
    )


@cache
def collected_test_names(marker_expression: str) -> frozenset[str]:
    """``path::function`` ids (parameters stripped) collected from the drift folder under ``-m``."""
    try:
        result = _collect(marker_expression)
    except subprocess.TimeoutExpired as error:
        message = f"collection timed out after {error.timeout}s"
        raise RuntimeError(message) from error
    if result.returncode != 0:
        message = f"collection failed ({result.returncode}):\n{result.stdout}\n{result.stderr}"
        raise RuntimeError(message)
    node_ids = [line for line in result.stdout.splitlines() if "::" in line]
    return frozenset(node_id.split("[")[0] for node_id in node_ids)


def canary_drift_command(workflow: Path = CI_WORKFLOW) -> str:
    """The ``run:`` command of the canary's drift step: exactly one in the ``django-main`` job.

    Other jobs (the integration job runs the drift folder too) are not looked at.
    """
    document = yaml.safe_load(workflow.read_text(encoding="utf-8")) or {}
    steps = _steps_of(document, workflow)
    matches = [
        str(step["run"]).strip()
        for step in steps
        if isinstance(step, dict) and runs_drift_folder(str(step.get("run", "")))
    ]
    if len(matches) != 1:
        message = f"expected exactly one drift step in {CANARY_JOB} of {workflow.name}, found {len(matches)}"
        raise RuntimeError(message)
    return matches[0]


def drift_step_requirements(workflow: Path = CI_WORKFLOW) -> dict[str, list[str | None]]:
    """``{job: [ASYNCOPATION_REQUIRE_MYSQLDB of each step that runs the drift folder]}``.

    ``None`` stands for a step that does not set the variable. A workflow in which no step runs the
    drift folder raises: an unmatched command must not read as "nothing to check".
    """
    document = yaml.safe_load(workflow.read_text(encoding="utf-8")) or {}
    jobs = document.get("jobs") if isinstance(document, dict) else None
    if not isinstance(jobs, dict):
        message = f"{workflow.name} has no jobs mapping"
        raise RuntimeError(message)
    found: dict[str, list[str | None]] = {}
    for name, job in jobs.items():
        steps = job.get("steps") if isinstance(job, dict) else None
        for step in steps if isinstance(steps, list) else []:
            if isinstance(step, dict) and runs_drift_folder(str(step.get("run", ""))):
                value = (step.get("env") or {}).get("ASYNCOPATION_REQUIRE_MYSQLDB")
                found.setdefault(name, []).append(None if value is None else str(value))
    if not found:
        message = (
            f"no step of {workflow.name} runs the drift folder; the CI check would pass vacuously"
        )
        raise RuntimeError(message)
    return found


def _steps_of(document: object, workflow: Path) -> list[object]:
    """The ``steps`` of the canary job; a malformed workflow raises instead of reading as empty."""
    jobs = document.get("jobs") if isinstance(document, dict) else None
    job = jobs.get(CANARY_JOB) if isinstance(jobs, dict) else None
    if not isinstance(job, dict):
        message = f"{workflow.name} has no mapping at jobs.{CANARY_JOB}"
        raise RuntimeError(message)
    steps = job.get("steps")
    return steps if isinstance(steps, list) else []


def write_workflow(
    path: Path, drift_steps: int, extra_lines: str = "", other_job_drift_steps: int = 0
) -> Path:
    """A minimal workflow: an ``integration`` job with ``other_job_drift_steps`` drift steps and a
    ``django-main`` job with ``drift_steps`` of them, followed by ``extra_lines`` (raw step text)."""
    step = f'      - run: uv run --no-sync pytest {DRIFT_DIR} -m "not django_pin"\n'
    path.write_text(
        "jobs:\n  integration:\n    steps:\n      - run: make test-int\n"
        + step * other_job_drift_steps
        + f"  {CANARY_JOB}:\n    steps:\n      - run: uv sync\n"
        + step * drift_steps
        + extra_lines,
        encoding="utf-8",
    )
    return path


def _collect(marker_expression: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            DRIFT_DIR,
            "-o",
            "addopts=",
            "--strict-markers",
            "-m",
            marker_expression,
            "--collect-only",
            "-q",
            "-p",
            "no:cacheprovider",
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        env={**os.environ, "PYTEST_ADDOPTS": ""},
        timeout=60,
    )
