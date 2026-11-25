import os

from django.core.asgi import get_asgi_application

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "harness.settings")
_django_application = get_asgi_application()


def _wrap_with_lifespan(app):
    from asyncopation.connection import registry
    from asyncopation.connection.asgi import LifespanApp

    lifespan_app = LifespanApp(app)

    async def application(scope, receive, send):
        async def report(message):
            await send(message)
            if message["type"] == "lifespan.shutdown.complete":
                # e2e: the pools are gone after shutdown (tests read this line from the server log)
                alias_stats = [registry.pool_stats(alias) for alias in ("default",)]
                print(
                    f"ASYNCOPATION_DIAG after-shutdown entries={len(registry.entries())} "
                    f"in_use={sum(s.in_use for s in alias_stats)} "
                    f"idle={sum(s.idle for s in alias_stats)} "
                    f"waiting={sum(s.waiting for s in alias_stats)}",
                    flush=True,
                )

        await lifespan_app(scope, receive, report if scope["type"] == "lifespan" else send)

    return application


# MySQL/MariaDB have no async driver until #7: there the plain Django app serves /sync and /core.
if os.environ.get("ASYNCOPATION_DB", "postgresql") == "postgresql":
    application = _wrap_with_lifespan(_django_application)
else:
    application = _django_application
