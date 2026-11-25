---
name: e2e-harness
description: End-to-end tests against a real uvicorn process serving the harness Django site on the test DB. Use when adding a user-visible scenario, testing ASGI integration, or reproducing a production-like bug.
---
# E2E harness

- Site: `harness/site` (urls in `urls.py`, scenarios in `views.py`, variants `/sync`, `/core`, `/aopt`).
- Fixture: `live_server_url` (tests/e2e/conftest.py) starts
  `uvicorn harness.site.asgi:application` on a free port with `ASYNCOPATION_DB_NAME` pointing at
  the pytest test database (seeded and committed), waits for `/health/`, and stops it after the
  session. Server logs are attached to failures.
- Client: `httpx.AsyncClient(base_url=live_server_url)` via the `http` fixture.
- Lifespan: the harness ASGI app is wrapped in `LifespanApp` on PostgreSQL (ADR-0004); `GET /diag/pool`
  returns the server process's registry (`entries`, `server_info`, per-alias `PoolStats`) and the
  wrapper prints `ASYNCOPATION_DIAG after-shutdown ...` after shutdown. Use the `lifespan_server` /
  `lifespan_http` fixtures (tests/e2e/conftest.py, tests/support/e2e_server.py) for lifecycle tests.

## Adding a scenario
1. Implement the view in all three variants in `harness/site/views.py` (same semantics) and
   route it in `urls.py`.
2. Add e2e tests (GWT, no control flow) asserting status and payload per variant using
   `@pytest.mark.parametrize("prefix", ["/sync", "/core", "/aopt"])`.
3. While Asyncopation cannot serve it yet, mark the `/aopt` case with
   `pytest.param("/aopt", marks=pytest.mark.xfail(strict=True, reason="Phase N"))`.
   `strict=True` turns the first passing run into a failure, forcing the marker's removal.
4. Register the scenario for load testing in `bench/k6/scenarios.js`.

Run: `make test-e2e` (needs `make db-up`).
