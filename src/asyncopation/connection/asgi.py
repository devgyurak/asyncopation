"""ASGI lifespan wrapper (ADR-0004 Decision 7). Django's ``ASGIHandler`` ignores ``lifespan``."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Iterable
from typing import Any

from asyncopation.connection.lifecycle import close, setup

type Scope = dict[str, Any]
type Message = dict[str, Any]
type Receive = Callable[[], Awaitable[Message]]
type Send = Callable[[Message], Awaitable[None]]
type ASGIApp = Callable[[Scope, Receive, Send], Awaitable[None]]


class LifespanApp:
    """Open the drivers at startup and close them at shutdown; everything else goes to ``app``.

    ``aliases`` limits ``setup()`` (default: every alias with a supported engine; an explicit alias
    that is unsupported fails the startup).
    """

    def __init__(self, app: ASGIApp, aliases: Iterable[str] | None = None) -> None:
        self.app = app
        self.aliases = None if aliases is None else list(aliases)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "lifespan":
            await self.app(scope, receive, send)
            return
        while True:
            message = await receive()
            if message["type"] == "lifespan.startup":
                try:
                    await setup(self.aliases)
                except Exception as exc:
                    await send({"type": "lifespan.startup.failed", "message": str(exc)})
                    return
                await send({"type": "lifespan.startup.complete"})
            elif message["type"] == "lifespan.shutdown":
                try:
                    await close()
                except Exception as exc:
                    await send({"type": "lifespan.shutdown.failed", "message": str(exc)})
                    return
                await send({"type": "lifespan.shutdown.complete"})
                return
