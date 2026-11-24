# ADR-0007: Sans-I/O cores for ported orchestration and the `_django` boundary

- Status: Accepted
- Date: 2026-11-24
- Deciders: maintainer (@devgyurak)
- Architect review: APPROVE WITH CHANGES (2026-11-24); required changes incorporated

## Context
Plan §4.4 requires rewriting Django code that interleaves I/O and logic as async:
`prefetch_related_objects` / `prefetch_one_level`, the deletion `Collector`, `get_or_create` /
`update_or_create`. Writing `async def` copies mixes our I/O into ported logic, makes them testable
only against a database, and makes upstream diffs hard to follow.

Facts that shape the core protocol: `prefetch_one_level` needs materialized instances, not rows;
`get_or_create` needs savepoints and must observe `IntegrityError`; `Collector.delete` sends
`pre_delete` / `post_delete`; Django's `CASCADE` calls `collector.collect(...)` synchronously
(`django/db/models/deletion.py`) and `SET(callable)` may run sync queries; "pure" helpers can hide
I/O - `GenericForeignKey.get_prefetch_querysets` calls `ContentType.objects.get_for_id` /
`get_for_model` (`django/contrib/contenttypes/fields.py`), which query on a cache miss.

Non-public Django imports are inventoried in `docs/django-api-ledger.toml` (guard A201) but may
appear anywhere in `src/`. import-linter cannot forbid subpackages of an external package such as
`django.db.models.sql`. `django.db.backends.postgresql.*` imports psycopg and
`django.db.backends.mysql.base` imports MySQLdb.

Prior art: psycopg 3 protocol generators driven by `wait()` / `wait_async()`; Ecto.Multi
(transaction as inspectable data); Hibernate Reactive's ~309 forked `Reactive*` classes and
django-async-backend's whole-module codegen (both release-pinned); ormar's drift test over private
SQLAlchemy attributes; SQLAlchemy's greenlet bridge.

## Options
### A - `async def` copies of Django functions
+ direct. - I/O and logic mixed; DB needed for every test; large diffs against upstream.

### B - greenlet bridge running Django's sync code
+ no port. - hidden I/O in sync frames (against non-negotiable 1), C-extension risk, Django's
`async_unsafe` and thread-local connection machinery still in the way.

### C - Whole-module code generation (django-async-backend)
+ mechanical. - large generated surface, hard to review, release-pinned.

### D - Sans-I/O generator cores + one async runner
+ logic written once, no `await`, unit-testable with a recording driver; a sync runner enables
differential tests of the core itself. - generator protocol is less familiar.

## Decision
**D**, plus a single boundary for Django internals.

1. **Core protocol.** Ported orchestration is written as generators that yield requests and
   receive results: `Execute(CompiledQuery)`, `Evaluate(queryset)` (materialized instances),
   `Batch([...])` (independent requests), `Savepoint` / `Atomic`, `SendSignal`. Errors (e.g.
   `IntegrityError`) are delivered with `generator.throw()`. One async runner in
   `asyncopation.engine` drives them against the current executor (ADR-0001).
2. **Fan-out.** The runner executes a `Batch` in parallel only with a borrowed executor, capped at
   a configurable width (default: pool max - 1), and sequentially with a pinned one.
3. **Deletion.** The `Collector` core produces an inspectable plan before execution. Django's
   built-in `on_delete` handlers (`CASCADE`, `PROTECT`, `RESTRICT`, `SET_NULL`, `SET_DEFAULT`,
   `DO_NOTHING`, `SET(value)`) are recognised by identity and implemented inside the core; unknown
   callables and `SET(callable)` raise a clear `NotImplementedError`.
4. **Helpers.** Django helpers are reused only after an I/O audit. Known hidden I/O: the
   ContentType cache (warmed by an async port before GFK prefetch).
5. **`asyncopation._django`** (new bottom layer) is the only package that imports non-public Django
   APIs; private attribute access (`qs._fetch_mode`, `compiler.klass_info`, `obj._state.peers`,
   `_prefetch_related_lookups`, ...) goes through `_django` accessors or is listed in the drift
   test. It is split per backend (`_django.postgresql`, `_django.mysql`, imported lazily) so
   importing `_django` never requires both driver libraries. A drift test asserts every symbol
   still exists with the expected signature on the pinned Django version. Enforced by guard A203
   (#30): a private Django import outside `asyncopation/_django/` is a violation. Direct private
   attribute access is not detectable without false positives; arch-guardian reviews it.
6. **Ledger semantics.** Ledger entries keep `used_by` listing the real consumers (not just
   `_django`); A202 staleness is checked against `_django`'s imports plus the drift-test list. The
   drift-test list is the ledger itself: `[[internal]]` entries (optionally with `signatures`) and
   `[[attribute]]` entries (private attributes accessed outside `_django`, with their owner class).
7. The **sync runner** over Django's own cursor lives in `tests/support/` (`src/` cannot import
   `django.db.connections`).

## Consequences
+ Fork surface stays small and reviewable; Django upgrades fail in one place.
- Contributors must learn the generator protocol; the runner is a critical, well-tested piece.
- Custom `on_delete` callables are unsupported until an async hook is designed.

## Validation
- unit: each core (prefetch level, collector plan, get_or_create) over a recording driver yields
  the expected requests in order; `IntegrityError` thrown into `get_or_create` leads to the get path.
- differential: the same core run by the sync runner and by the async runner matches Django's
  `prefetch_related_objects` / `Collector` results; CASCADE deletes children; a custom `on_delete`
  callable raises.
- unit: drift test over every `_django` symbol on the pinned Django version.
