# Benchmark baselines

`<backend>.json` (e.g. `postgresql.json`) holds the reference result for `bench.compare`.
Baselines are machine-specific: record the machine in `meta.machine` and only replace a
baseline from the same machine class, on `main`, following the `perf-bench` skill.
No baseline is committed until the first Asyncopation read path exists (Phase 1).

The in-process micro benchmark (`make bench-micro`) writes `bench/results/micro-<ts>.json` and
stores no baseline: its driver cases (`driver.*` on PostgreSQL, `mysqldriver.*` on MySQL and
MariaDB, chosen by `ASYNCOPATION_DB`) are compared as per-round paired ratios inside one run.
