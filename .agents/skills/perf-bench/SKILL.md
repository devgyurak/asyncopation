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
Asyncopation cases are reported as `skipped` until the API exists. Compile cases
(`compile_scope`, `as_sql_scoped`, `as_sql_unscoped`; PostgreSQL only) measure the cost of the
synchronous `compile_scope` swap and need no database round trip.

### Driver cases
`bench/micro.py` also runs PostgreSQL driver cases (implementation in `bench/driver_micro.py`)
named `driver.<group>.<impl>.t<N>` with `N` = 1, 8 or 64 concurrent tasks:
- groups: `select1` (`SELECT 1`), `wide` (one row with 15 columns), `execute` (`execute()` with
  rowcount), `tx` (`begin` / statement / `commit`), `stream` (200 rows in chunks of 50);
- impls: `raw` (`pool.connection()` + cursor on a pool built from the driver's own
  configuration), `getconn` (raw `getconn` / `putconn`, the cheapest baseline, present in every
  group), `driver` (`driver.connection()` + the driver API);
- flags (see `uv run python -m bench.micro --help`): `--driver-seconds` (seconds per
  `driver.*` round, default 1.0), `--driver-rounds` (interleaved rounds, default 7), `--only`
  (comma-separated case names with the first dot-separated part removed, e.g.
  `--only select1.driver.t8`; the same filter as for the other cases);
- no-I/O cases `driver.noio.<impl>.t1` and `driver.noio-yield.<impl>.t1` (impls `getconn` and
  `driver`, run with 1 task only): fake pool and cursor objects take the database out, so the
  difference measures the driver's own cost; `noio-yield` uses a fake that yields
  (`await asyncio.sleep(0)`). Treat the result as a LOWER BOUND of the driver's own cost;
- output columns: `CPU µs/op` (client CPU per operation, the stable metric), `paired ratio
  (IQR)` (per-round paired ratio vs the baseline: median and inter-quartile range over the
  rounds), `driver-getconn µs` (driver minus `getconn` CPU per operation, shown with its IQR
  only for the noio groups), `load` (min-max 1-minute load average over the rounds) and a
  `note` column (error or skip reason). A case without samples has status `no-samples`. The baselines of a
  selected case always run too, so its ratio can be computed.

Compare ratios, not absolute times, and rerun when the load is high. Real-DB ratios within about
+-5% are noise. Driver overhead (driver minus `getconn`), as ranges: `noio` (plain fake) about
0.5-0.7 us/op, `noio-yield` about 0.6-1.4 us/op (the IQR reaches about 2.4 us under load, so
rerun on a quiet machine).

## Load (k6, minutes)
```bash
make bench BENCH_ARGS="--configs wsgi,asgi-core,asyncopation --scenarios single,gather,nplus1,write --duration 30s --vus 50"
```
`bench/run.py` per config: prepares `bench_asyncopation` DB (create + seed), starts the server
(gunicorn for `wsgi`, uvicorn for ASGI configs, same worker count), waits for `/health/`,
uses `--lifespan on` for the `asyncopation` config on PostgreSQL (its pools are opened at startup by `LifespanApp`, ADR-0004, so the first requests do not pay for the open and the probe) and `--lifespan off` for `asgi-core` (it does not use the registry). On MySQL/MariaDB the `asyncopation` config is reported `unavailable` (no async driver until #7), and a server that fails to start is reported as an `error` row instead of aborting the run. The runner
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
