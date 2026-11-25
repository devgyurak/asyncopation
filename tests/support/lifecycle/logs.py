"""Helpers that read the connection layer's log records."""

from __future__ import annotations

import logging
from typing import Any

LAZY_LOGGER = "asyncopation.connection"


def lazy_warnings(caplog: Any) -> list[str]:
    """Messages logged at WARNING or above by the connection package's logger."""
    return [
        r.getMessage()
        for r in caplog.records
        if r.name == LAZY_LOGGER and r.levelno >= logging.WARNING
    ]


def capture_logs(caplog: Any) -> None:
    caplog.set_level(logging.DEBUG, logger=LAZY_LOGGER)


def all_mention(messages: list[str], fragment: str) -> bool:
    return all(fragment in message for message in messages)


def errors_logged(caplog: Any) -> list[str]:
    return [
        r.getMessage()
        for r in caplog.records
        if r.name == LAZY_LOGGER and r.levelno >= logging.ERROR
    ]


def info_messages(caplog: Any) -> list[str]:
    return [
        r.getMessage()
        for r in caplog.records
        if r.name == LAZY_LOGGER and r.levelno == logging.INFO
    ]


def mention_count(messages: list[str], fragment: str) -> int:
    return sum(1 for m in messages if fragment in m)
