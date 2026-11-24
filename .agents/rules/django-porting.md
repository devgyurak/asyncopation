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
4. **Reuse helpers only after an I/O audit** (`RelatedPopulator`, `get_converters`), and record
   them in the ledger. "Pure-looking" helpers can hide I/O: `GenericForeignKey.get_prefetch_querysets`
   queries ContentType on a cache miss. Ported orchestration is a sans-I/O generator core
   (ADR-0007), not an `async def` copy.
5. **Import internals only in `asyncopation._django`** and ledger every one in `docs/django-api-ledger.toml` (`module`, `names`,
   `used_by` (the real consumers), `reason`, `verified_against`). The guard rejects unknown internals (A201),
   private Django imports outside `_django` (A203) and stale entries (A202: an `[[internal]]` item not
   imported under `_django`, an `[[attribute]]` never accessed). Add `signatures = { Name = "<str(inspect.signature)>" }`
   for callables whose signature you depend on, and register every Django private attribute you access
   directly (`qs._fetch_mode`) as an `[[attribute]]` (`owner`, `names`, `used_by`, `reason`,
   `verified_against`); the drift test (`tests/unit/django_drift`) checks all of it against installed Django.
   The guard cannot detect private attribute access - arch review does.
6. **Differential test every port** against the sync implementation on all backends.
7. When the `django-main` CI canary breaks, update ports and bump `verified_against`.
