"""ASGI lifespan wrapper (ADR-0004 Decision 7). Django's ``ASGIHandler`` ignores ``lifespan``."""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import Awaitable, Callable, Iterable
from typing import Any

from asyncopation.connection.lifecycle import close, setup

type Scope = dict[str, Any]
type Message = dict[str, Any]
type Receive = Callable[[], Awaitable[Message]]
type Send = Callable[[Message], Awaitable[None]]
type ASGIApp = Callable[[Scope, Receive, Send], Awaitable[None]]

logger = logging.getLogger("asyncopation.connection")

_DONE: Message = {"type": "asyncopation.lifespan.done"}


class _InnerLifespan:
    """The wrapped app's own ``lifespan`` run, driven through two queues.

    An app that cannot handle ``lifespan`` (Django's ``ASGIHandler`` raises for any scope but
    ``http``) ends before it ever receives a message; it then simply does not take part. An app
    that crashes after it received a message is a failure of that phase. The run is a task that
    :meth:`finish` always awaits (no fire-and-forget); it is cancelled once the lifespan ends.
    """

    def __init__(self, app: ASGIApp, scope: Scope) -> None:
        self._to_app: asyncio.Queue[Message] = asyncio.Queue()
        self._from_app: asyncio.Queue[Message] = asyncio.Queue()
        self._consumed = False
        self._error: Exception | None = None
        self._task = asyncio.create_task(self._run(app, scope))

    async def _receive(self) -> Message:
        message = await self._to_app.get()
        self._consumed = True
        return message

    async def _run(self, app: ASGIApp, scope: Scope) -> None:
        try:
            await app(scope, self._receive, self._from_app.put)
        except Exception as exc:
            self._error = exc
            if not self._consumed:
                logger.debug("asyncopation: the wrapped app does not handle lifespan: %r", exc)
        finally:
            self._from_app.put_nowait(_DONE)  # wakes a waiting exchange()

    def _died(self, message: Message) -> Message | None:
        """The failure reply for a phase the app died in, ``None`` when it never took part."""
        if self._error is None or not self._consumed:
            return None
        text = f"{type(self._error).__name__}: {self._error}"
        return {"type": f"{message['type']}.failed", "message": text}

    async def exchange(self, message: Message) -> Message | None:
        """Send ``message``; the app's reply, or ``None`` when the app is not taking part."""
        if self._task.done():
            return self._died(message)
        self._to_app.put_nowait(message)
        prefix = f"{message['type']}."
        while True:
            reply = await self._from_app.get()
            if reply is _DONE:
                return self._died(message)
            if reply["type"].startswith(prefix):  # a stale reply of an earlier phase is dropped
                return reply

    async def finish(self) -> None:
        self._task.cancel()  # a no-op once it ended
        await asyncio.gather(self._task, return_exceptions=True)


class LifespanApp:
    """Open the drivers at startup and close them at shutdown; everything else goes to ``app``.

    ``aliases`` limits ``setup()`` (default: every alias with a supported engine; an explicit alias
    that is unsupported fails the startup). The ``lifespan`` scope is also forwarded to ``app``, so
    an app with its own startup/shutdown work still gets it: startup runs ``setup()`` first, then
    the app's startup (if that fails, the drivers are closed again); shutdown runs the app's
    shutdown first, then ``close()`` (which runs even when the app's shutdown fails or is
    cancelled). An app that does not support ``lifespan`` is ignored.
    """

    def __init__(self, app: ASGIApp, aliases: Iterable[str] | None = None) -> None:
        if isinstance(aliases, str):
            raise TypeError("LifespanApp(aliases) takes an iterable of alias names, not a str")
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
                if await self._startup(inner, message, send):
                    return
            elif message["type"] == "lifespan.shutdown":
                await self._shutdown(inner, message, send)
                return

    async def _startup(self, inner: _InnerLifespan, message: Message, send: Send) -> bool:
        """Run startup; ``True`` when it failed (the serve loop ends)."""
        try:
            await setup(self.aliases)
        except Exception as exc:
            await send({"type": "lifespan.startup.failed", "message": str(exc)})
            return True
        try:
            reply = await inner.exchange(message)
        except BaseException:  # cancelled while the app starts: do not leave the pools open
            with contextlib.suppress(Exception):
                await close()
            raise
        if reply is not None and reply["type"] == "lifespan.startup.failed":
            try:
                await close()
            except Exception:
                logger.exception("asyncopation: closing the drivers after a failed startup failed")
            await send(reply)
            return True
        await send({"type": "lifespan.startup.complete"})
        return False

    async def _shutdown(self, inner: _InnerLifespan, message: Message, send: Send) -> None:
        failure: Message | None = None
        try:
            reply = await inner.exchange(message)
            if reply is not None and reply["type"].endswith(".failed"):
                failure = reply
        finally:  # close() runs even when the app's shutdown hangs and is cancelled
            try:
                await close()
            except Exception as exc:
                logger.exception("asyncopation: close() failed at lifespan shutdown")
                failure = failure or {"type": "lifespan.shutdown.failed", "message": str(exc)}
        await send(failure or {"type": "lifespan.shutdown.complete"})
