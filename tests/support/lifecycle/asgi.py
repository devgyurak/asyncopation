"""ASGI helpers: a fake wrapped app and a lifespan driver for `LifespanApp`."""

from __future__ import annotations

import asyncio
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

    @property
    def types(self) -> list[str]:
        return [m["type"] for m in self.sent]


async def run_lifespan(app: Any) -> LifespanRun:
    """Drive `app` through startup then shutdown; snapshot the registry at every message."""
    events = iter([{"type": "lifespan.startup"}, {"type": "lifespan.shutdown"}])
    run = LifespanRun([], {})

    async def receive() -> dict[str, Any]:
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


async def lifespan_with(app: Any) -> LifespanRun:
    return await run_lifespan(app)


class LifespanAwareApp:
    """A wrapped app with its own lifespan: records `(event, registry aliases then)` in order."""

    def __init__(
        self, startup_failure: str | None = None, shutdown_failure: str | None = None
    ) -> None:
        self.startup_failure = startup_failure
        self.shutdown_failure = shutdown_failure
        self.events: list[tuple[str, list[str]]] = []

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        if scope["type"] != "lifespan":
            return
        while True:
            message = await receive()
            if message["type"] == "lifespan.startup":
                self.events.append(("app.startup", entry_aliases()))
                if self.startup_failure is not None:
                    await send({"type": "lifespan.startup.failed", "message": self.startup_failure})
                    return
                await send({"type": "lifespan.startup.complete"})
            elif message["type"] == "lifespan.shutdown":
                self.events.append(("app.shutdown", entry_aliases()))
                if self.shutdown_failure is not None:
                    await send(
                        {"type": "lifespan.shutdown.failed", "message": self.shutdown_failure}
                    )
                else:
                    await send({"type": "lifespan.shutdown.complete"})
                return


class NonLifespanApp:
    """Like Django's `ASGIHandler`: anything but `http` raises."""

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        raise ValueError("Django can only handle ASGI/HTTP connections, not lifespan.")


async def run_lifespan_counting_tasks(app: Any) -> tuple[LifespanRun, int, int]:
    """`run_lifespan`, plus the number of tasks alive before and after (no task may leak)."""
    import asyncio

    before = len(asyncio.all_tasks())
    run = await run_lifespan(app)
    return run, before, len(asyncio.all_tasks())


class CrashingApp:
    """A lifespan app that raises: `crash_on` is `startup`, `shutdown` or `after-startup`."""

    def __init__(self, crash_on: str, error: Exception | None = None) -> None:
        self.crash_on = crash_on
        self.error = error or TypeError(f"bad {crash_on}")

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        if scope["type"] != "lifespan":
            return
        while True:
            message = await receive()
            if message["type"] == "lifespan.startup":
                if self.crash_on == "startup":
                    raise self.error
                await send({"type": "lifespan.startup.complete"})
                if self.crash_on == "after-startup":
                    raise self.error
            elif message["type"] == "lifespan.shutdown":
                if self.crash_on == "shutdown":
                    raise self.error
                await send({"type": "lifespan.shutdown.complete"})
                return


class StaleFailureApp:
    """Answers startup with `complete`, then (stale) `startup.failed`: the second reply is still
    queued when the shutdown phase starts."""

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        if scope["type"] != "lifespan":
            return
        while True:
            message = await receive()
            if message["type"] == "lifespan.startup":
                await send({"type": "lifespan.startup.complete"})
                await send({"type": "lifespan.startup.failed", "message": "stale"})
            elif message["type"] == "lifespan.shutdown":
                await send({"type": "lifespan.shutdown.complete"})
                return


class HangingApp:
    """A lifespan app that never replies in `hang_on` (`startup` or `shutdown`)."""

    def __init__(self, hang_on: str) -> None:
        self.hang_on = hang_on
        self.hung = asyncio.Event()

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        if scope["type"] != "lifespan":
            return
        while True:
            message = await receive()
            phase = message["type"].removeprefix("lifespan.")
            if phase == self.hang_on:
                self.hung.set()
                await asyncio.Event().wait()
            await send({"type": f"{message['type']}.complete"})
            if phase == "shutdown":
                return


async def _hung(app: HangingApp) -> bool:
    return app.hung.is_set()


async def run_lifespan_cancelled_while_app_hangs(app: Any, hanging: HangingApp) -> LifespanRun:
    """Drive `app` (which wraps `hanging`) until `hanging` hangs, then cancel the lifespan task."""
    from tests.support.aio import wait_until

    events = iter([{"type": "lifespan.startup"}, {"type": "lifespan.shutdown"}])
    run = LifespanRun([], {})

    async def receive() -> dict[str, Any]:
        return next(events)

    async def send(message: dict[str, Any]) -> None:
        run.sent.append(message)
        run.snapshots[message["type"]] = entry_aliases()

    task = asyncio.create_task(app({"type": "lifespan"}, receive, send))
    await wait_until(lambda: _hung(hanging))
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)
    return run
