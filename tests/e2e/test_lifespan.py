"""ASGI lifespan e2e: pools open at startup and are closed after shutdown (ADR-0004 Validation)."""

import pytest

from harness.settings import VENDOR
from tests.support.e2e_server import ServerProcess

PG_ONLY = pytest.mark.skipif(
    VENDOR != "postgresql",
    reason="PostgreSQL pool counting through pg_stat_activity; the harness serves plain Django "
    "on MySQL/MariaDB until the executor integration arrives with issue #7b",
)


@PG_ONLY
async def test_startup_opens_the_pool_before_any_request(lifespan_http):
    # when
    diag = (await lifespan_http.get("/diag/pool")).json()
    # then
    assert diag["entries"] == ["default"]
    assert diag["server_info"] is True


@PG_ONLY
async def test_the_registry_reports_a_pool_with_connections_after_startup(lifespan_http):
    # when
    diag = (await lifespan_http.get("/diag/pool")).json()
    # then
    stats = diag["pool"]["default"]
    assert stats["in_use"] + stats["idle"] > 0


@PG_ONLY
def test_shutdown_completes_the_lifespan_protocol(lifespan_server: ServerProcess):
    # when
    lifespan_server.stop()
    # then
    logged = lifespan_server.logged("Application startup complete", "Application shutdown complete")
    assert logged is True


@PG_ONLY
def test_the_server_had_sessions_open_when_the_shutdown_began(
    lifespan_server: ServerProcess,
):
    # when
    lifespan_server.stop()
    # then
    before, _after = lifespan_server.sessions_around_shutdown()
    assert before >= 1


@PG_ONLY
def test_the_server_reported_no_sessions_after_the_shutdown_completed(
    lifespan_server: ServerProcess,
):
    # when
    lifespan_server.stop()
    # then
    before, after = lifespan_server.sessions_around_shutdown()
    assert before >= 1  # the precondition: there was something to close
    assert after == 0


@pytest.mark.parametrize("prefix", ["/sync", "/core"])
async def test_existing_variants_still_serve_with_the_lifespan_wrapper(
    lifespan_http, book_pk, prefix
):
    # when
    response = await lifespan_http.get(f"{prefix}/book/{book_pk}/")
    # then
    assert response.status_code == 200
    assert response.json()["id"] == book_pk


async def test_health_is_served_through_the_wrapper(lifespan_http, readonly_db):
    # when
    response = await lifespan_http.get("/health/")
    # then
    assert response.status_code == 200


@PG_ONLY
def test_startup_opens_the_pools_without_the_lazy_open_warning(lifespan_server: ServerProcess):
    # when
    clean = lifespan_server.lacks("lazily on first use")
    # then
    assert clean is True
