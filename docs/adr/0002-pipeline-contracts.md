# ADR-0002: Evaluation pipeline contracts and the `results_iter` seam

- Status: Accepted
- Date: 2026-11-24
- Deciders: maintainer (@devgyurak)
- Architect review: APPROVE WITH CHANGES (2026-11-24); required changes incorporated

## Context
Plan §4.1 fixes *what* is reused (Django's `as_sql()`) and *what* is replaced (execution), but
not what the stages exchange. Without typed stage values the evaluation code would pass Django
compiler objects and cursors around ad hoc.

Django 6.1 (`django/db/models/query.py`, `ModelIterable.__iter__`) runs
`compiler.execute_sql(MULTI)` and then `compiler.results_iter(results)`.
`SQLCompiler.results_iter(results=None, ...)` (`django/db/models/sql/compiler.py`) calls
`execute_sql` only when `results is None`; given rows it applies `get_converters` /
`apply_converters` and composite-field handling, with no I/O. `execute_sql` itself also carries
non-I/O behaviour: `EmptyResultSet` short-circuit, trimming rows to `col_count` when
`has_extra_select`, and SINGLE-row trimming.

Prior art: Hibernate Reactive feeds async rows into unchanged core result processing through a
`ResultSetAdaptor`; ormar caches a frozen `StatementPlan`; Prisma (ADR 002) keeps plans immutable.
django-turbo-orm builds rows with `Model.from_db` directly and never runs `from_db_value`.

## Options
### A - Port the whole result path (own converters + materialization)
+ full control. - duplicates Django logic, drifts with every release, easy to miss converters.

### B - Reuse Django's result path through `results_iter(results=...)`
+ converters, `from_db_value`, composite pks behave exactly as in sync; the `ModelIterable` port
shrinks to the post-execute part. - depends on non-public compiler methods (ledger + drift test).

## Decision
**B.** The evaluation pipeline is:

```
QuerySet (Django, lazy)
  -> compile     (sync)   CompiledQuery: frozen; the compiler instance, sql, params,
                          result_kind (MULTI | SINGLE | ROWCOUNT | EMPTY), col_count,
                          has_extra_select, returning
  -> execute     (async)  driver-level values: list[tuple] chunks, ExecResult, or a stream
                          context manager - via an executor (ADR-0001) and driver (ADR-0003)
  -> materialize (sync)   compiler.results_iter(results=[chunk]) -> ported post-execute
                          ModelIterable logic -> Model.from_db
```

1. **Layers.** A new layer `asyncopation.engine` (above `transaction`, below `fetch`) defines
   `CompiledQuery`, builds it from a QuerySet using the compile-only wrapper (`compile` provides
   the wrapper; how compilers reach it is ADR-0008), adapts executor output, and owns the
   materializer and the sans-I/O runner (ADR-0007). Executors in `connection` speak only
   driver-level types, so nothing points upward. The public facade (`asyncopation.query`) moves to
   the top, below `testing`, because evaluation calls prefetch and `adelete` calls deletion.
2. **Execute contract.** The execute stage reproduces `execute_sql`'s non-I/O behaviour:
   `EmptyResultSet` or empty SQL -> `result_kind=EMPTY`, no I/O, empty result; rows trimmed to
   `col_count` when `has_extra_select`; SINGLE trimmed to `col_count`. `as_sql()` is called exactly
   once per compiler (it mutates compiler state), and `CompiledQuery` carries that same compiler.
3. **Scope of the seam.** The `results_iter` seam covers MULTI evaluation: `ModelIterable`,
   `ValuesIterable`, `ValuesListIterable`, `FlatValuesListIterable`, `NamedValuesListIterable`.
   SINGLE paths (`acount` via `Query.get_aggregation`, `aexists` via `has_results`) are separate,
   ledgered ports. Write compilers (insert returning / `last_insert_id`, update related updates and
   `pre_sql_setup` queries) are orchestration and follow ADR-0007.
4. **Streaming.** The materializer sets up once per query (`select`, `klass_info`, `init_list`,
   `related_populators`, `known_related_objects`, one shared `peers` list) and then calls
   `results_iter(results=[chunk])` for each fetched chunk; sharing `peers` across chunks matches
   sync `FETCH_PEERS` semantics.
5. Rows handed to `results_iter` are already normalized by the driver (ADR-0003). `CompiledQuery`
   is not cached across evaluations in v1; caching is a later, benchmarked change.

## Consequences
+ Differential correctness of converters comes for free. + `fetch`, `prefetch`, `deletion` reuse
`engine` without importing the public facade.
- Ledger entries and drift tests for `results_iter`, `get_converters`, `_get_from_db`,
`get_related_populators`, `compiler.klass_info` / `annotation_col_map`, `_state.peers`.
- Two extra layers in import-linter (`engine`, and `_django` from ADR-0007).
- Depends on ADR-0008 (Accepted): aggregate/update compilers run inside `compile_scope`; I/O-bearing
  steps are ported cores (ADR-0008 Decision 4).

## Validation
- `tests/integration/test_harness_selfcheck.py::test_asyncopation_list_matches_sync` passes on all
  backends, including `FieldZoo` fields with `from_db_value`.
- differential: `alist` / `acount` on `qs[:3]`, `distinct()`, `annotate()` + count (aggregate
  compiler); `extra(select=...)` + `order_by` (col_count trimming); `filter(pk__in=[])` issues zero
  queries; chunked `aiterator` under `FETCH_PEERS`.
- differential: a composite-pk model must produce the same rows, tuple pks included, through
  asyncopation as through the sync ORM (`alist` / `aget`, tracked in #6). `BookEdition`
  (`CompositePrimaryKey("book", "number")`) is in `harness/catalog` (migration, seed,
  `EXPECTED_COUNTS`) with reference cases in `test_harness_selfcheck.py`; the cases that exercise
  the tuple conversion path are those selecting `pk` explicitly (`values("pk", ...)`,
  `values_list("pk", ...)`).
- unit: materializer over a recording driver returns the same instances as `results_iter` on
  fixed rows.
