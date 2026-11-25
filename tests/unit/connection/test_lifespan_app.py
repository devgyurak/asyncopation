"""`LifespanApp`: lifespan startup/shutdown drive setup()/close(); every other scope is delegated."""

import pytest

from asyncopation.connection.asgi import LifespanApp
from tests.support.lifecycle.asgi import (
    FakeApp,
    LifespanAwareApp,
    NonLifespanApp,
    call_with_scope,
    run_lifespan,
    run_lifespan_counting_tasks,
)


async def test_startup_opens_the_drivers_and_reports_complete(configured):
    # given
    app = LifespanApp(FakeApp())
    # when
    run = await run_lifespan(app)
    # then
    assert (run.types[0], run.snapshots["lifespan.startup.complete"]) == (
        "lifespan.startup.complete",
        ["default", "other"],
    )


async def test_shutdown_closes_the_drivers_and_reports_complete(configured):
    # given
    app = LifespanApp(FakeApp())
    # when
    run = await run_lifespan(app)
    # then
    assert (run.types[1], run.snapshots["lifespan.shutdown.complete"]) == (
        "lifespan.shutdown.complete",
        [],
    )


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
    assert (run.types, "boom: open" in run.sent[0]["message"]) == (
        ["lifespan.startup.failed"],
        True,
    )


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
    from asyncopation.connection import configure

    fake.use_unsupported_engine("default")
    configure()
    app = LifespanApp(FakeApp())
    # when
    run = await run_lifespan(app)
    # then
    assert run.snapshots["lifespan.startup.complete"] == ["other"]


async def test_aliases_limit_the_startup(configured):
    # given
    app = LifespanApp(FakeApp(), aliases=["other"])
    # when
    run = await run_lifespan(app)
    # then
    assert run.snapshots["lifespan.startup.complete"] == ["other"]


async def test_an_explicit_unsupported_alias_fails_the_startup(fake):
    # given
    from asyncopation.connection import configure

    fake.use_unsupported_engine("default")
    configure()
    app = LifespanApp(FakeApp(), aliases=["default"])
    # when
    run = await run_lifespan(app)
    # then
    assert run.types == ["lifespan.startup.failed"]


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


async def test_lifespan_scope_is_forwarded_to_the_wrapped_app(configured):
    # given
    inner = FakeApp()
    # when
    await run_lifespan(LifespanApp(inner))
    # then
    assert inner.scope_types == ["lifespan"]


async def test_the_apps_startup_runs_after_setup_and_its_shutdown_before_close(configured):
    # given
    inner = LifespanAwareApp()
    # when
    await run_lifespan(LifespanApp(inner))
    # then
    assert inner.events == [
        ("app.startup", ["default", "other"]),
        ("app.shutdown", ["default", "other"]),
    ]


async def test_a_lifespan_aware_app_does_not_change_the_messages_we_send(configured):
    # given
    inner = LifespanAwareApp()
    # when
    run = await run_lifespan(LifespanApp(inner))
    # then
    assert run.types == ["lifespan.startup.complete", "lifespan.shutdown.complete"]


async def test_an_app_startup_failure_is_reported_instead_of_complete(configured):
    # given
    inner = LifespanAwareApp(startup_failure="app boom")
    # when
    run = await run_lifespan(LifespanApp(inner))
    # then
    assert run.types == ["lifespan.startup.failed"]
    assert run.sent[0]["message"] == "app boom"


async def test_an_app_startup_failure_closes_our_drivers_before_reporting(configured):
    # given
    inner = LifespanAwareApp(startup_failure="app boom")
    # when
    run = await run_lifespan(LifespanApp(inner))
    # then
    assert run.snapshots["lifespan.startup.failed"] == []
    assert configured.recorder.closes == 2


async def test_an_app_shutdown_failure_wins_over_our_complete(configured):
    # given
    inner = LifespanAwareApp(shutdown_failure="app stuck")
    # when
    run = await run_lifespan(LifespanApp(inner))
    # then
    assert run.types == ["lifespan.startup.complete", "lifespan.shutdown.failed"]
    assert run.sent[1]["message"] == "app stuck"


async def test_an_app_shutdown_failure_still_closes_our_drivers(configured):
    # given
    inner = LifespanAwareApp(shutdown_failure="app stuck")
    # when
    run = await run_lifespan(LifespanApp(inner))
    # then
    assert run.snapshots["lifespan.shutdown.failed"] == []


async def test_an_app_shutdown_failure_wins_over_our_own_close_failure(configured):
    # given
    configured.script.fail_close = True
    inner = LifespanAwareApp(shutdown_failure="app stuck")
    # when
    run = await run_lifespan(LifespanApp(inner))
    # then
    assert run.sent[1]["message"] == "app stuck"


async def test_an_app_that_cannot_do_lifespan_is_ignored(configured):
    # when
    run = await run_lifespan(LifespanApp(NonLifespanApp()))
    # then
    assert run.types == ["lifespan.startup.complete", "lifespan.shutdown.complete"]


async def test_our_own_startup_failure_does_not_start_the_app(configured):
    # given
    configured.script.fail_opens = 1
    inner = LifespanAwareApp()
    # when
    run = await run_lifespan(LifespanApp(inner))
    # then
    assert run.types == ["lifespan.startup.failed"]
    assert inner.events == []


@pytest.mark.parametrize(
    "inner",
    [
        pytest.param(FakeApp(), id="returns-at-once"),
        pytest.param(LifespanAwareApp(), id="lifespan-aware"),
        pytest.param(LifespanAwareApp(startup_failure="x"), id="startup-fails"),
        pytest.param(NonLifespanApp(), id="raises"),
    ],
)
async def test_the_lifespan_run_leaves_no_task_behind(configured, inner):
    # when
    _run, before, after = await run_lifespan_counting_tasks(LifespanApp(inner))
    # then
    assert after == before


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
