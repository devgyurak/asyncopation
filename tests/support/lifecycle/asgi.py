"""ASGI helpers: a fake wrapped app and a lifespan driver for `LifespanApp`."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from tests.support.lifecycle.helpers import entry_aliases


class FakeApp:
    """The wrapped ASGI app: records every delegated call."""

    def __init__(self) -> None:
        self.calls: list[tuple[dict[str, Any], Any, Any]] = []

    @property
    def scope_types(self) -> list[str]:
        return [scope["type"] for scope, _receive, _send in self.calls]

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        self.calls.append((scope, receive, send))


@dataclass
class LifespanRun:
    sent: list[dict[str, Any]]
    snapshots: dict[str, list[str]]  # message type -> registry aliases at that moment
    consumed: int = 0  # messages the app received

    @property
    def types(self) -> list[str]:
        return [m["type"] for m in self.sent]


async def run_lifespan(app: Any, *messages: dict[str, Any]) -> LifespanRun:
    """Drive `app` with `messages` (default: startup then shutdown); snapshot the registry at every
    message it sends."""
    events = iter(messages or [{"type": "lifespan.startup"}, {"type": "lifespan.shutdown"}])
    run = LifespanRun([], {})

    async def receive() -> dict[str, Any]:
        run.consumed += 1
        return next(events)

    async def send(message: dict[str, Any]) -> None:
        run.sent.append(message)
        run.snapshots[message["type"]] = entry_aliases()

    await app({"type": "lifespan"}, receive, send)
    return run


async def call_with_scope(app: Any, scope: dict[str, Any]) -> tuple[Any, Any]:
    async def receive() -> dict[str, Any]:
        return {"type": "noop"}

    async def send(message: dict[str, Any]) -> None:
        return None

    await app(scope, receive, send)
    return receive, send
