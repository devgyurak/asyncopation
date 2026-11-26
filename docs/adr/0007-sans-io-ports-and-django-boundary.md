# ADR-0007: Sans-I/O cores for ported orchestration and the `_django` boundary

- Status: Accepted
- Date: 2026-11-24
- Deciders: maintainer (@devgyurak)
- Architect review: APPROVE WITH CHANGES (2026-11-24); required changes incorporated
- Amended: 2026-11-25 (#5, PR #61) - Decision 5 (`_django.transaction` re-exports `TransactionManagementError`, the one name of the banned `django.db.transaction` that guard A101 lets through, only in `_django/transaction.py`); approved by: maintainer (@devgyurak), 2026-11-25 in the PR #61 review; architect verdict: APPROVE WITH CHANGES (architect review 2026-11-25, changes applied)
- Amended: 2026-11-25 (#47, PR #63) - Validation (the `_django` drift test also runs in the `django-main` canary job, minus the `django_pin` tests); approved by: maintainer (@devgyurak), 2026-11-25 in the PR #63 review; architect verdict: AMEND OK (architect review 2026-11-25)
- Amended: 2026-11-25 (#6, PR #67) - Decisions 1 and 5 (the first sans-I/O requests are `Compile`, `Execute` and `Convert` and `run_core` owns the connection side; ported functions are split at the I/O seam into `_django/<topic>.py` and `engine/cores.py`; the pure rewrite runs inside `compile_scope`, the conversion of fetched rows outside it; the `close()` of a failed or cancelled run runs inside `compile_scope`, and an `Exception` it raises, such as a `finally` that raises or a core that ignores `GeneratorExit`, is attached to the original exception with `add_note` and never replaces it); approved by: maintainer (@devgyurak), to be confirmed in the PR #67 review; architect verdict: APPROVE (architect review 2026-11-26 of the final wording, including the D1 close/note text)

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
   *Amended 2026-11-25 (#6, PR #67): the first requests are `Compile(query, kind, elide_empty)`, `Execute(compiled)` and `Convert(compiled, outer_query, row, empty_set_result)`. `Compile` carries a Django `Query` (not a QuerySet) and is answered by a `CompiledQuery`; `Execute` is answered by the rows (MULTI) or the first row or `None` (SINGLE); `Convert` is answered by the converted `{alias: value}` dict of an aggregation row (converters and user `from_db_value` code). `asyncopation.engine.runner.run_core(core, alias)` owns the connection side: it opens the alias (`ensure_driver`), resolves the current executor (ADR-0001) and the compile-only wrapper once, and compiles with `autocommit = not executor.pinned`; a core never sees an executor, a wrapper or a driver. Every synchronous stretch of a core and every `Compile` run inside `compile_scope` (never across an `await`), and so does the final `close()`, which exists for failed or cancelled runs only (a core that returned or raised is finished); `Convert` runs outside any scope, because `from_db_value` is user code that must see Django's real connection registry, as in the materializer (ADR-0008 Validation). An exception raised while serving a request is delivered with `generator.throw()`; `CancelledError` is not: the runner ends the run with `close()`, which raises `GeneratorExit` in the core, so a core must not catch `GeneratorExit` (an `except BaseException` must re-raise); a `finally` may run cleanup, but an error it raises (or a core that ignores `GeneratorExit`) never replaces the original exception: the runner attaches an `Exception` from `close()` to the original as a note (async-correctness rule 4; the note is formatted defensively, a broken `__repr__` falls back to the type name); a `compile_scope` enter or exit error (`ScopeContextError`, a programming error) is not suppressed and propagates chained to the original, and a `BaseException` raised while closing (a nested cancellation, `KeyboardInterrupt`) propagates instead of being noted. `CompiledQuery.compiler` is an opaque handle a core may pass back to `_django` converters. `alist` still runs `engine.evaluate.evaluate_list` directly; it moves behind an `Evaluate` request when the first core needs materialized instances (prefetch, Phase 4); `Batch`, `Savepoint` and `SendSignal` arrive with their consumers.*
2. **Fan-out.** The runner executes a `Batch` in parallel only with a borrowed executor, capped at
   a configurable width (default: pool max - 1), and sequentially with a pinned one.
3. **Deletion.** The `Collector` core produces an inspectable plan before execution. Django's
   built-in `on_delete` handlers (`CASCADE`, `PROTECT`, `RESTRICT`, `SET_NULL`, `SET_DEFAULT`,
   `DO_NOTHING`, `SET(value)`) are recognised by identity and implemented inside the core; unknown
   callables and `SET(callable)` raise a clear `NotImplementedError`.
4. **Helpers.** Django helpers are reused only after an I/O audit. Known hidden I/O: the
   ContentType cache (warmed by an async port before GFK prefetch).
5. **`asyncopation._django`** (new bottom layer) is the only package that imports non-public Django
   APIs. Private attribute access (`qs._fetch_mode`, `compiler.klass_info`, `obj._state.peers`,
   `_prefetch_related_lookups`, ...) goes through `_django` accessors, and every such attribute is
   registered in the ledger and drift-tested. It is split per backend (`_django.postgresql`, `_django.mysql`, imported lazily) so
   importing `_django` never requires both driver libraries. A drift test asserts every symbol
   still exists with the expected signature on the pinned Django version. Enforced by guard A203
   (#30): a private Django import outside `asyncopation/_django/` is a violation. Direct private
   attribute access cannot be detected without false positives, so arch-guardian reviews it.
   *Amended 2026-11-25 (#5, PR #61): `_django/transaction.py` re-exports Django's
   `TransactionManagementError` (ledger `[[internal]]` entry), which a pinned executor raises for a
   broken transaction (ADR-0001 6c). Guard A101 keeps `django.db.transaction` banned everywhere
   except exactly `from django.db.transaction import TransactionManagementError` in
   `asyncopation/_django/transaction.py`; `atomic` and the rest of that module stay banned.*
   *Amended 2026-11-25 (#6, PR #67): a ported Django function that interleaves logic with one I/O call is split at the I/O seam. The pure halves, which read non-public `Query` attributes, live in `asyncopation/_django/<topic>.py` (here `aggregation.py`: `prepare_aggregation`, `finish_aggregation`, with a `ported = true` ledger entry and header); the generator that yields the requests lives in `asyncopation/engine/cores.py`. `_django` functions contain no request and no `await`. The pure rewrite (`prepare_aggregation`) runs synchronously inside `compile_scope`, so any expression that reads `connections[alias]` sees the compile-only wrapper; the conversion half (`finish_aggregation`) is served by the runner as a `Convert` request outside the scope. This is the model for `pre_sql_setup` and the Collector (Phase 5).*
6. **Ledger semantics.** Ledger entries keep `used_by` listing the real consumers (not just
   `_django`); A202 flags an `[[internal]]` entry that nothing under `_django` imports (unless it is
   `ported`; a `ported` entry is checked instead for a `used_by` file that exists under
   `src/asyncopation/` and carries a `# Ported from django/<module path>.py` comment) and an
   `[[attribute]]` never accessed in `src/`. The drift-test list is the ledger itself: `[[internal]]` entries (optionally with `signatures`) and
   `[[attribute]]` entries (every private attribute we access directly, inside `_django` or not, with its owner class). Code that
   is ported but never imported (e.g. the MySQL backend) is marked `ported = true`.
   *Amended 2026-11-25 (#50, PR #57): A202 now also checks `ported` entries (each `used_by` file exists under `src/asyncopation/` and carries a `# Ported from django/<module path>.py` comment); approved by: maintainer (@devgyurak), 2026-11-25 in the PR #57 review.*
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
  *Amended 2026-11-25 (#47, PR #63): the same suite (`tests/unit/django_drift`) also runs in the CI `django-main` canary job against Django main with `-m "not django_pin"`. The two tests marked `django_pin` assert the pinned minor itself and run only on the pinned version. The canary is `continue-on-error`: an early warning of upstream drift, not a gate.*
