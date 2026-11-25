# ADR-0002: Evaluation pipeline contracts and the `results_iter` seam

- Status: Accepted
- Date: 2026-11-24
- Deciders: maintainer (@devgyurak)
- Architect review: APPROVE WITH CHANGES (2026-11-24); required changes incorporated
- Amended: 2026-11-25 (#6, PR #65) - Decision 2 (`CompiledQuery` also carries `alias` and `values_names`, so the pipeline diagram box and the field description change; the alias is resolved once per evaluation; an EMPTY query does no statement I/O but still opens the alias first), Decision 4 (the key names of `values()` rows are read before `as_sql()`; the compile stage compiles a copy of the QuerySet's Query, so overlapping evaluations of one QuerySet do not see each other's `as_sql()` mutations), new Validation items; approved by: maintainer (@devgyurak), to be confirmed in the PR #65 review; architect verdict: APPROVE WITH CHANGES (architect review 2026-11-25, changes applied)

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
*Amended 2026-11-25 (#6, PR #65): the `CompiledQuery` box also holds `alias` and `values_names`
(Decision 2).*

1. **Layers.** A new layer `asyncopation.engine` (above `transaction`, below `fetch`) defines
   `CompiledQuery`, builds it from a QuerySet using the compile-only wrapper (`compile` provides
   the wrapper; how compilers reach it is ADR-0008), adapts executor output, and owns the
   materializer and the sans-I/O runner (ADR-0007). Executors in `connection` speak only
   driver-level types, so nothing points upward. The public facade (`asyncopation.query`) moves to
   the top, below `testing`, because evaluation calls prefetch and `adelete` calls deletion.
2. **Execute contract.** The execute stage reproduces `execute_sql`'s non-I/O behaviour:
   `EmptyResultSet` or empty SQL -> `result_kind=EMPTY`, no statement I/O (*Amended 2026-11-25 (#6, PR #65): the alias is still opened first*), empty result; rows trimmed to
   `col_count` when `has_extra_select`; SINGLE trimmed to `col_count`. `as_sql()` is called exactly
   once per compiler (it mutates compiler state), and `CompiledQuery` carries that same compiler.
   *Amended 2026-11-25 (#6, PR #65): `CompiledQuery` also carries `alias` and `values_names`. The
   alias is read once per evaluation (`queryset.db` runs the database routers, and a stateful
   router must not answer differently for the compile and the materialize stage), by
   `evaluate_list`, and compile and materialize reuse it. An EMPTY query does no statement I/O,
   but `evaluate_list` still opens the alias first (ADR-0004 Decision 4), so `.none()` or
   `pk__in=[]` may open the pool.*
3. **Scope of the seam.** The `results_iter` seam covers MULTI evaluation: `ModelIterable`,
   `ValuesIterable`, `ValuesListIterable`, `FlatValuesListIterable`, `NamedValuesListIterable`.
   SINGLE paths (`acount` via `Query.get_aggregation`, `aexists` via `has_results`) are separate,
   ledgered ports. Write compilers (insert returning / `last_insert_id`, update related updates and
   `pre_sql_setup` queries) are orchestration and follow ADR-0007.
4. **Streaming.** The materializer sets up once per query (`select`, `klass_info`, `init_list`,
   `related_populators`, `known_related_objects`, one shared `peers` list) and then calls
   `results_iter(results=[chunk])` for each fetched chunk; sharing `peers` across chunks matches
   sync `FETCH_PEERS` semantics. *Amended 2026-11-25 (#6, PR #65): the key names of `values()` /
   `values_list(named=True)` rows are read from the Query before `as_sql()` (Django reads them
   before `execute_sql`): compiling a combined query ordered by an unselected column adds
   `__orderbycol<N>` annotations that are not row keys. The compile stage stores them in
   `CompiledQuery.values_names`. The compile stage also works on `Query.clone()` of the
   QuerySet's Query, with the shared `selected` dict copied: `as_sql()` mutates the Query it runs
   on, so two overlapping `alist()` calls on one wrapper (or any later clone of an already
   evaluated one) would otherwise compile a half-polluted Query and fail with `IndexError`.*
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
- *Amended 2026-11-25 (#6, PR #65):* unit: with a router that answers differently on every call,
  `evaluate_list` asks it once and the instances carry that alias in `_state.db`.
- *Amended 2026-11-25 (#6, PR #65):* differential: `values()` / `values_list()` / `flat` / `named` of
  a `union()` ordered by an unselected column, and of `distinct()` ordered by an unselected column,
  return the same rows and keys as the sync ORM (no `__orderbycol<N>` keys, extra columns trimmed).
- *Amended 2026-11-25 (#6, PR #65):* unit and integration: compiling one union+`values()` QuerySet
  twice, and `gather(w.alist(), w.alist())` on one wrapper, give the sync ORM's rows and leave the
  QuerySet's Query (`selected`, annotations) untouched.
