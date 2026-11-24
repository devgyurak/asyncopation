# Benchmark baselines

`<backend>.json` (e.g. `postgresql.json`) holds the reference result for `bench.compare`.
Baselines are machine-specific: record the machine in `meta.machine` and only replace a
baseline from the same machine class, on `main`, following the `perf-bench` skill.
No baseline is committed until the first Asyncopation read path exists (Phase 1).
