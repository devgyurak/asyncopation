---
name: port-django-internals
description: Procedure for porting or reusing Django internals (ModelIterable, prefetch_one_level, Collector, fetch modes, get_or_create...) into async code with attribution, ledger entries and drift protection. Use before copying or importing any non-public Django code.
---
# Porting Django internals

1. **Research**: delegate to `django-researcher` with the entry point (e.g.
   `QuerySet._fetch_all -> ModelIterable.__iter__`). Get file:line call chain and I/O points.
2. **Decide reuse vs port** for each function:
   - no I/O -> import and reuse (ledger entry only);
   - I/O mixed with logic -> port into the owning layer.
3. **Port faithfully**: copy the function, keep names/branch order/comments, convert only the
   I/O lines to `await` calls on our layers. Add the header:
   ```python
   # Ported from django/db/models/query.py::ModelIterable.__iter__ @ 6.1.3
   # Changes: cursor iteration -> await driver.fetchmany(); otherwise identical.
   ```
4. **Ledger**: add or extend an `[[internal]]` entry in `docs/django-api-ledger.toml`
   (`module`, `names`, `used_by`, `reason`, `verified_against = "<django version>"`; optional
   `signatures = { "Name.method" = "<str(inspect.signature(obj))>" }` with dotted keys quoted;
   `ported = true` when you port it without importing it). Import it only inside
   `src/asyncopation/_django/` (A203). Every private attribute you access directly (`qs._fetch_mode`), inside `_django` or not,
   gets an `[[attribute]]` entry (`owner` dotted class path, `names`, `used_by`, `reason`,
   `verified_against`). `tests/unit/django_drift` verifies every entry against installed Django.
5. **Tests**: differential tests covering every branch you ported (backends included).
6. **Verify**: `uv run python -m tools.guards` (no A201/A202/A203), `uv run lint-imports`.

When Django is upgraded: for each ledger entry, diff upstream between the old and new
`verified_against` versions (`git diff 6.1.3..6.1.4 -- django/db/models/query.py` in a Django
checkout), apply relevant changes to ports, bump `verified_against`.
