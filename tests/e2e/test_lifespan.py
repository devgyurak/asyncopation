"""ASGI lifespan e2e: pools open at startup and are closed after shutdown (ADR-0004 Validation)."""

import pytest

from tests.support.e2e_server import ServerProcess


async def test_startup_opens_the_pool_before_any_request(lifespan_http):
    # when
    diag = (await lifespan_http.get("/diag/pool")).json()
    # then
    assert diag["entries"] == ["default"]
    assert diag["server_info"] is True


async def test_pool_holds_connections_after_startup(lifespan_http):
    # when
    diag = (await lifespan_http.get("/diag/pool")).json()
    # then
    stats = diag["pool"]["default"]
    assert stats["in_use"] + stats["idle"] > 0


def test_shutdown_completes_the_lifespan_protocol(lifespan_server: ServerProcess):
    # when
    lifespan_server.stop()
    # then
    logged = lifespan_server.logged("Application startup complete", "Application shutdown complete")
    assert logged is True


def test_shutdown_closes_the_pools_and_empties_the_registry(lifespan_server: ServerProcess):
    # when
    lifespan_server.stop()
    # then
    assert lifespan_server.after_shutdown() == (0, 0, 0, 0)


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


def test_startup_opens_the_pools_without_the_lazy_open_warning(lifespan_server: ServerProcess):
    # when
    clean = lifespan_server.lacks("lazily on first use")
    # then
    assert clean is True
