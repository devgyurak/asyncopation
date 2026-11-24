---
name: perf-bench
description: Benchmark harness - in-process micro-benchmarks and k6 load tests comparing WSGI+sync, ASGI+core async and ASGI+Asyncopation, with DB connection and memory sampling and baseline comparison. Use for hot-path changes, performance questions and releases.
---
# Performance benchmarks

## Micro (in-process, seconds)
```bash
make bench-micro                       # = uv run python -m bench.micro
uv run python -m bench.micro --iterations 2000 --only aget
```
Measures per-call latency of core async vs Asyncopation for single lookups, gathered lookups
and list evaluation against the seeded DB. Writes `bench/results/micro-<ts>.json`.
Asyncopation cases are reported as `skipped` until the API exists.

### Driver cases
`bench/micro.py` also runs PostgreSQL driver cases (implementation in `bench/driver_micro.py`)
named `driver.<group>.<impl>.t<N>` with `N` = 1, 8 or 64 concurrent tasks:
- groups: `select1` (`SELECT 1`), `wide` (one row with 15 columns), `execute` (`execute()` with
  rowcount), `tx` (`begin` / statement / `commit`), `stream` (200 rows in chunks of 50);
- impls: `raw` (`pool.connection()` + cursor on a pool built from the driver's own
  configuration: the baseline), `getconn` (raw `getconn` / `putconn`, the cheapest baseline,
  `select1` only), `driver` (`driver.connection()` + the driver API);
- flags (see `uv run python -m bench.micro --help`): `--driver-seconds` (seconds per
  `driver.*` round, default 1.0), `--driver-rounds` (interleaved rounds, default 7), `--only`
  (comma-separated case names with the first dot-separated part removed, e.g.
  `--only select1.driver.t8`; the same filter as for the other cases);
- output columns: `CPU us/op` (client CPU per operation, the stable metric), `paired ratio
  (IQR)` (per-round paired ratio vs the baseline, median and inter-quartile range over the
  rounds) and `load` (min-max 1-minute load average over the rounds; the baselines of a selected
  case are always run too, so its ratio can be computed). Compare ratios, not absolute times, and rerun when
  the load is high.

## Load (k6, minutes)
```bash
make bench BENCH_ARGS="--configs wsgi,asgi-core,asyncopation --scenarios single,gather,nplus1,write --duration 30s --vus 50"
```
`bench/run.py` per config: prepares `bench_asyncopation` DB (create + seed), starts the server
(gunicorn for `wsgi`, uvicorn for ASGI configs, same worker count), waits for `/health/`,
samples DB connections (`pg_stat_activity` / `information_schema.processlist`) and server RSS
every 250 ms, runs k6 in docker (`docker compose --profile bench run k6`), and writes
`bench/results/load-<ts>.json` plus a markdown table to stdout.

Metrics: p50/p95/p99 latency, req/s, error rate, peak/avg DB connections, peak RSS.

## Comparing
```bash
uv run python -m bench.compare bench/baseline/postgresql.json bench/results/load-<ts>.json --threshold 0.10
```
Exit code 1 on a regression beyond the threshold (p95 up or throughput down).

## Updating the baseline
Only on a quiet machine, on `main`, after a reviewed perf change:
copy the result to `bench/baseline/<backend>.json`, fill `machine` (CPU, RAM, OS, docker
resources) and commit with `perf: update <backend> baseline` explaining why.

## Hygiene
Close other heavy apps; run each config at least twice and discard the warm-up run; never
compare numbers from different machines or backends.
