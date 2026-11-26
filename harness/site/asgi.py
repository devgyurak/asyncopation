import asyncio
import os

from django.conf import settings
from django.core.asgi import get_asgi_application

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "harness.settings")

# e2e: tag this process's database sessions so tests (and the diagnostics below) can count them
_APP_NAME = os.environ.get("ASYNCOPATION_APP_NAME")
if _APP_NAME and os.environ.get("ASYNCOPATION_DB", "postgresql") == "postgresql":
    settings.DATABASES["default"].setdefault("OPTIONS", {})["application_name"] = _APP_NAME

_django_application = get_asgi_application()


async def _session_count() -> int:
    """pg_stat_activity sessions tagged with this process's application name (e2e diagnostics)."""
    import psycopg
    from django.db import connections

    cfg = connections["default"].settings_dict
    async with await psycopg.AsyncConnection.connect(
        dbname=cfg["NAME"],
        user=cfg["USER"],
        password=cfg["PASSWORD"],
        host=cfg["HOST"],
        port=cfg["PORT"],
        application_name="asyncopation-diag",
        autocommit=True,
    ) as connection:
        cursor = await connection.execute(
            "SELECT count(*) FROM pg_stat_activity WHERE application_name = %s", [_APP_NAME]
        )
        row = await cursor.fetchone()
    return int(row[0]) if row else 0


async def _safe_session_count() -> int:
    """-1 when the diagnostic connection fails: diagnostics must never break the real shutdown."""
    try:
        return await _session_count()
    except Exception:
        return -1


async def _count_after_the_server_reaped_closed_sessions() -> int:
    count = await _safe_session_count()
    for _ in range(40):
        if count == 0:
            break
        await asyncio.sleep(0.1)
        count = await _safe_session_count()
    return count


def _wrap_with_lifespan(app):
    from asyncopation.connection.asgi import LifespanApp

    lifespan_app = LifespanApp(app)

    async def application(scope, receive, send):
        if scope["type"] != "lifespan" or not _APP_NAME:
            await lifespan_app(scope, receive, send)
            return
        state = {"before": -1}

        async def counting_receive():
            message = await receive()
            if message["type"] == "lifespan.shutdown":
                state["before"] = await _safe_session_count()  # the pools are still open here
            return message

        async def report(message):
            if message["type"] == "lifespan.shutdown.complete":
                # close() has finished; print before telling the server, which exits right after
                after = await _count_after_the_server_reaped_closed_sessions()
                # e2e: tests read this line from the server log (sessions before close / after)
                print(
                    f"ASYNCOPATION_DIAG sessions before-close={state['before']} after-shutdown={after}",
                    flush=True,
                )
            await send(message)

        await lifespan_app(scope, counting_receive, report)

    return application


# MySQL/MariaDB have no async read path until #7b (the driver exists since #7a): there the plain Django app serves /sync and /core.
if os.environ.get("ASYNCOPATION_DB", "postgresql") == "postgresql":
    application = _wrap_with_lifespan(_django_application)
else:
    application = _django_application
