---
description: How to depend on and port Django internals without silent drift
paths:
  - "src/**"
  - "docs/django-api-ledger.toml"
---
# Django porting rules

1. **Read the installed source, not memory.** Django is pinned (`django>=6.1,<6.2`); read
   `.venv/lib/python*/site-packages/django/...` for the exact version (`uv run python -c
   "import django; print(django.__version__)"`). Delegate large reads to `django-researcher`.
2. **Port, don't paraphrase.** Keep the ported function's structure, names and branch order
   so a future diff against upstream is mechanical. Only change what is needed for async:
   replace I/O calls with awaits on our layer; keep pure-Python parts identical.
3. **Attribute every port** with a header comment:
   `# Ported from django/db/models/query.py::prefetch_one_level @ 6.1.3 - <what changed>`.
4. **Reuse pure helpers instead of copying** when they do no I/O (`RelatedPopulator`,
   `get_prefetch_querysets`, `get_converters`), and record them in the ledger.
5. **Ledger every internal import** in `docs/django-api-ledger.toml` (`module`, `names`,
   `used_by`, `reason`, `verified_against`). The guard rejects unknown internals (A201) and
   stale entries (A202).
6. **Differential test every port** against the sync implementation on all backends.
7. When the `django-main` CI canary breaks, update ports and bump `verified_against`.
