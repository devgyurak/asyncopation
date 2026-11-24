---
name: differential-testing
description: Sync-vs-async differential testing harness - snapshots, the differential fixture, Hypothesis QuerySet strategies and how to extend them for new QuerySet features. Use when adding read APIs or debugging result mismatches.
---
# Differential testing

The correctness oracle is Django's own sync ORM: the same QuerySet evaluated sync and through
Asyncopation must give identical snapshots on every backend.

## Pieces (tests/support/)
- `snapshot.py` - `snapshot(obj)` turns a model instance into a hashable tuple
  `(app_label.Model, ((attname, type_name, value)...), ((annotation, type_name, value)...),
  ((cache_name, related_snapshot | None)...))`;
  annotations are the instance's public non-field attributes (`annotation_names()` lists them).
  The 4th element holds objects cached in `_state.fields_cache` (`select_related`, single-valued prefetch), recursively and sorted by cache
  name: a relation that was not loaded has no entry, one loaded as `None` is `(name, None)`,
  and an object already on the recursion path (1:1 cycle) becomes `("<ref>", label, pk)`.
  `_prefetched_objects_cache` (multi-valued prefetch: reverse FK, M2M, GenericRelation) is not
  covered yet (#17).
  `snapshot_result(rows)` keeps order.
  Types are part of the snapshot on purpose: drivers differ (Decimal vs float, naive vs aware
  datetime, `memoryview` vs `bytes`, JSON decoding).
- `differential.py` - `Outcome(snapshot, error)` (`.rows` for list results) and `run_differential(build, evaluator)`.
  `build` is a zero-arg callable returning a **fresh** QuerySet (never share a QuerySet between
  the two sides: result caches would leak). Exceptions are captured by type, so
  `DoesNotExist` on one side and a row on the other is a mismatch.
- `evaluators.py` - `asyncopation_evaluator` (the subject) and `reference_evaluator`
  (Django core async API; used for the harness self-check).
- `strategies.py` - Hypothesis strategies producing `QuerySetSpec`s (filters, excludes,
  ordering, slicing, select_related, annotations picked by name from `ANNOTATIONS` and usable in
  the ordering) and `build_queryset(spec)`; `annotated_spec()` builds example-based specs.

## Adding coverage for a new QuerySet feature
1. Extend `QuerySetSpec` + `queryset_specs()` with the new dimension (keep generation
   deterministic and bounded: small lists, fields from the catalog models only).
2. Teach `build_queryset` to apply it.
3. Add targeted example-based tests for edge cases Hypothesis is unlikely to hit.
4. Run `uv run pytest tests/integration -k differential` on all three backends.

## Debugging a mismatch
- Re-run with `--hypothesis-seed=<seed>` from the failure output; the shrunk spec is printed.
- `str(build().query)` shows the SQL Django generates; compare with the captured async SQL.
- Type-only mismatch -> driver conversion issue: check `get_converters` and the driver's
  type adapters before touching query code.
