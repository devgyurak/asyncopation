"""ASGI lifespan wrapper (ADR-0004 Decision 7). Django's ``ASGIHandler`` ignores ``lifespan``."""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Awaitable, Callable, Iterable
from typing import Any

from asyncopation.connection.lifecycle import close, setup

type Scope = dict[str, Any]
type Message = dict[str, Any]
type Receive = Callable[[], Awaitable[Message]]
type Send = Callable[[Message], Awaitable[None]]
type ASGIApp = Callable[[Scope, Receive, Send], Awaitable[None]]

_DONE: Message = {"type": "asyncopation.lifespan.done"}


class _InnerLifespan:
    """The wrapped app's own ``lifespan`` run, driven through two queues.

    An app that cannot handle ``lifespan`` (Django's ``ASGIHandler`` raises for any scope but
    ``http``) ends at once; it then simply does not take part. The run is a task that
    :meth:`finish` always awaits (no fire-and-forget).
    """

    def __init__(self, app: ASGIApp, scope: Scope) -> None:
        self._to_app: asyncio.Queue[Message] = asyncio.Queue()
        self._from_app: asyncio.Queue[Message] = asyncio.Queue()
        self._task = asyncio.create_task(self._run(app, scope))

    async def _run(self, app: ASGIApp, scope: Scope) -> None:
        try:
            await app(scope, self._to_app.get, self._from_app.put)
        except Exception:  # lifespan not supported (or the app crashed): it does not take part
            pass
        finally:
            self._from_app.put_nowait(_DONE)  # wakes a waiting exchange()

    async def exchange(self, message: Message) -> Message | None:
        """Send ``message``; the app's reply, or ``None`` when the app is not taking part."""
        if self._task.done():
            return None
        self._to_app.put_nowait(message)
        reply = await self._from_app.get()
        return None if reply is _DONE else reply

    async def finish(self) -> None:
        self._task.cancel()  # a no-op once it ended
        await asyncio.gather(self._task, return_exceptions=True)


class LifespanApp:
    """Open the drivers at startup and close them at shutdown; everything else goes to ``app``.

    ``aliases`` limits ``setup()`` (default: every alias with a supported engine; an explicit alias
    that is unsupported fails the startup). The ``lifespan`` scope is also forwarded to ``app``, so
    an app with its own startup/shutdown work still gets it: startup runs ``setup()`` first, then
    the app's startup (if that fails, the drivers are closed again); shutdown runs the app's
    shutdown first, then ``close()``. An app that does not support ``lifespan`` is ignored.
    """

    def __init__(self, app: ASGIApp, aliases: Iterable[str] | None = None) -> None:
        self.app = app
        self.aliases = None if aliases is None else list(aliases)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "lifespan":
            await self.app(scope, receive, send)
            return
        inner = _InnerLifespan(self.app, scope)
        try:
            await self._serve(inner, receive, send)
        finally:
            await inner.finish()

    async def _serve(self, inner: _InnerLifespan, receive: Receive, send: Send) -> None:
        while True:
            message = await receive()
            if message["type"] == "lifespan.startup":
                try:
                    await setup(self.aliases)
                except Exception as exc:
                    await send({"type": "lifespan.startup.failed", "message": str(exc)})
                    return
                reply = await inner.exchange(message)
                if reply is not None and reply["type"] == "lifespan.startup.failed":
                    with contextlib.suppress(Exception):
                        await close()
                    await send(reply)
                    return
                await send({"type": "lifespan.startup.complete"})
            elif message["type"] == "lifespan.shutdown":
                reply = await inner.exchange(message)
                failure = reply if reply is not None and reply["type"].endswith(".failed") else None
                try:
                    await close()
                except Exception as exc:
                    failure = failure or {
                        "type": "lifespan.shutdown.failed",
                        "message": str(exc),
                    }
                await send(failure or {"type": "lifespan.shutdown.complete"})
                return
