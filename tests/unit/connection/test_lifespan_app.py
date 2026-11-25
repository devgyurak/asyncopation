"""`LifespanApp`: lifespan startup/shutdown drive setup()/close(); every other scope is delegated."""

import pytest

from asyncopation.connection import configure
from asyncopation.connection.asgi import LifespanApp
from tests.support.lifecycle.asgi import FakeApp, call_with_scope, run_lifespan
from tests.support.lifecycle.logs import capture_logs, exception_logs


async def test_startup_opens_the_drivers_and_reports_complete(configured):
    # given
    app = LifespanApp(FakeApp())
    # when
    run = await run_lifespan(app)
    # then
    assert run.snapshots["lifespan.startup.complete"] == ["default", "other"]


async def test_shutdown_closes_the_drivers_and_reports_complete(configured):
    # given
    app = LifespanApp(FakeApp())
    # when
    run = await run_lifespan(app)
    # then
    assert run.snapshots["lifespan.shutdown.complete"] == []


async def test_lifespan_sends_exactly_startup_then_shutdown_messages(configured):
    # given
    app = LifespanApp(FakeApp())
    # when
    run = await run_lifespan(app)
    # then
    assert run.types == ["lifespan.startup.complete", "lifespan.shutdown.complete"]


async def test_failed_startup_reports_startup_failed_with_the_message(configured):
    # given
    configured.script.fail_opens = 1
    app = LifespanApp(FakeApp())
    # when
    run = await run_lifespan(app)
    # then
    assert "boom: open" in run.sent[0]["message"]


async def test_failed_startup_leaves_no_entries(configured):
    # given
    configured.script.fail_opens = 1
    app = LifespanApp(FakeApp())
    # when
    run = await run_lifespan(app)
    # then
    assert run.snapshots["lifespan.startup.failed"] == []


async def test_unsupported_engine_is_skipped_by_the_default_startup(fake):
    # given
    fake.use_unsupported_engine("default")
    configure()
    app = LifespanApp(FakeApp())
    # when
    run = await run_lifespan(app)
    # then
    assert run.snapshots["lifespan.startup.complete"] == ["other"]


async def test_failed_shutdown_reports_shutdown_failed_with_the_message(configured):
    # given
    configured.script.fail_close = True
    app = LifespanApp(FakeApp())
    # when
    run = await run_lifespan(app)
    # then
    assert (run.types[1], "boom: close" in run.sent[1]["message"]) == (
        "lifespan.shutdown.failed",
        True,
    )


@pytest.mark.parametrize("scope_type", ["http", "websocket", "other"])
async def test_other_scopes_are_delegated_untouched(configured, scope_type):
    # given
    inner = FakeApp()
    scope = {"type": scope_type, "path": "/x"}
    # when
    receive, send = await call_with_scope(LifespanApp(inner), scope)
    # then
    assert inner.calls == [(scope, receive, send)]


async def test_delegated_scopes_do_not_open_drivers(configured):
    # given
    inner = FakeApp()
    # when
    await call_with_scope(LifespanApp(inner), {"type": "http"})
    # then
    assert configured.recorder.built == []


async def test_startup_without_configure_reports_startup_failed_naming_the_app(fake):
    # given
    app = LifespanApp(FakeApp())
    # when
    run = await run_lifespan(app)
    # then
    assert "INSTALLED_APPS" in run.sent[0]["message"]


async def test_a_failing_close_at_shutdown_is_logged_with_its_traceback(configured, caplog):
    # given
    capture_logs(caplog)
    configured.script.fail_close = True
    app = LifespanApp(FakeApp())
    # when
    await run_lifespan(app)
    # then
    assert len(exception_logs(caplog, "lifespan shutdown")) == 1


async def test_the_wrapped_app_never_receives_the_lifespan_scope(configured):
    # given
    inner = FakeApp()
    # when
    await run_lifespan(LifespanApp(inner))
    # then
    assert inner.scope_types == []


async def test_an_unknown_lifespan_message_is_ignored_and_the_loop_continues(configured):
    # given
    app = LifespanApp(FakeApp())
    unknown = {"type": "lifespan.unknown"}
    # when
    run = await run_lifespan(
        app, unknown, {"type": "lifespan.startup"}, {"type": "lifespan.shutdown"}
    )
    # then
    assert run.types == ["lifespan.startup.complete", "lifespan.shutdown.complete"]


async def test_nothing_is_sent_after_a_failed_startup(configured):
    # given
    configured.script.fail_opens = 1
    app = LifespanApp(FakeApp())
    # when
    run = await run_lifespan(app)
    # then
    assert run.types == ["lifespan.startup.failed"]


async def test_a_failed_startup_does_not_consume_the_shutdown_message(configured):
    # given
    configured.script.fail_opens = 1
    app = LifespanApp(FakeApp())
    # when
    run = await run_lifespan(app, {"type": "lifespan.startup"}, {"type": "lifespan.shutdown"})
    # then
    assert run.consumed == 1
