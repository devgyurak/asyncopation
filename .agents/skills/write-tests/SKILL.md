---
name: write-tests
description: How to write tests in this repo - Given/When/Then markers, zero control flow, fixtures (readonly_db, writable_db, differential), parametrize/Hypothesis instead of loops. Use whenever creating or editing files under tests/.
---
# Writing tests

The full rule set is `.agents/rules/testing.md`; `tools/guards` enforces it (T1xx-T3xx).

## Template
```python
import pytest
from functools import partial

from harness.catalog.models import Book

pytestmark = pytest.mark.django_db  # lets fixtures unblock DB access


@pytest.mark.parametrize(("title", "expected_count"), [("book-1", 1), ("missing", 0)])
async def test_filter_by_title_matches_sync(readonly_db, differential, title, expected_count):
    # given
    build = partial(Book.objects.filter, title=title)
    # when
    result = await differential(build)
    # then
    assert result.async_outcome == result.sync_outcome
    assert len(result.sync_outcome.rows) == expected_count
```

## Replacing control flow
| Instead of | Use |
|---|---|
| `for x in cases:` | `@pytest.mark.parametrize` with literal values (ids for readability) |
| random/combinatorial inputs | Hypothesis `@given(strategy)` from `tests/support/strategies.py` |
| `if vendor == "mysql":` | separate test + `@pytest.mark.skipif(VENDOR != "mysql", reason=...)` |
| `try/except` to check errors | `# when / then` + `with pytest.raises(Exc):` |
| `[x async for x in it]` | `await collect(it)` from `tests.support` |
| list/dict comprehensions | a builder in `tests/support/` or a literal |
| `x if cond else y` | parametrize both cases |
| waiting with sleep | `await wait_until(predicate)` in support (add it if missing) |

## Fixtures (tests/conftest.py)
- `readonly_db` - committed deterministic seed (`harness/seed.py`); never write.
- `writable_db` - same seed; reset (committed) after the test.
- Scratch tables (driver-level write tests) - instead of `writable_db`, a fixture creates and
  drops its own table via a `tests/support` helper (see `scratch` in
  `tests/integration/drivers/conftest.py`, helpers in `tests/support/drivers/`). Allowed because
  the committed seed stays untouched; table names carry a per-run random suffix so concurrent
  checkouts sharing one test database cannot collide.
- `differential` - runs a QuerySet factory through sync Django and Asyncopation, returns both
  outcomes as comparable snapshots. `reference_differential` uses Django core async instead of
  Asyncopation (harness self-check).
- `live_server_url` (e2e) - uvicorn serving `harness.site` against the test DB.

## Markers
`integration`/`e2e` are automatic by directory. Add `@pytest.mark.django_pin` only to a test that
asserts the pinned Django version (it fails by design on Django main); the `django-main` canary
runs `tests/unit/django_drift -m "not django_pin"`. Ledger and dependency drift checks stay unmarked.

## Checklist before handing off
1. `uv run python -m tools.guards tests/...` clean.
2. `uv run pytest <file> -q` passes (or fails for the intended reason in the red phase).
3. Each test asserts one behaviour and its name says which.
