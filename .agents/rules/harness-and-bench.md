---
description: Shared harness (models, seed, site), e2e and benchmark conventions
paths:
  - "harness/**"
  - "bench/**"
  - "tests/e2e/**"
---
# Harness and benchmark rules

- `harness/` is shared by integration tests, e2e and benchmarks: `settings.py` (backend via
  `ASYNCOPATION_DB`), `catalog/` models, `seed.py` (deterministic dataset), `site/` (Django
  site with `/sync`, `/core`, `/aopt` variants of each scenario).
- Backend-specific models (e.g. `contrib.postgres` fields) go in a separate app installed by
  `harness/settings.py` only for that vendor (`harness/pgcatalog`, PostgreSQL), never in `catalog`:
  MySQL/MariaDB migrations must not see extension operations. Its seed counts are
  `PG_EXPECTED_COUNTS`.
- Changing models or seed sizes changes every expectation: update `EXPECTED_COUNTS` and run the
  full integration suite on all backends.
- A new benchmark scenario must exist in **all three** variants with identical semantics,
  otherwise comparisons are meaningless. Register it in `bench/k6/scenarios.js`.
- Benchmark numbers are only comparable on the same machine, same backend, same commit of
  `harness/`. Never commit raw results; update `bench/baseline/<backend>.json` only via the
  `perf-bench` skill with the machine description filled in.
- A change on a hot path (`query/`, `connection/`, `drivers/`) must include a micro-benchmark
  comparison (`make bench-micro`) in the PR description.
