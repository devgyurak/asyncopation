---
name: architecture-guard
description: Run, interpret and extend the mechanical architecture/test/language guards (import-linter contracts, tools/guards A1xx/A2xx/T/L codes). Use when a guard fails, when adding a layer/module, or when a new invariant should be enforced.
---
# Architecture guard

## What enforces what
| Tool | Command | Enforces |
|---|---|---|
| import-linter | `uv run lint-imports` | layer order, compile purity, driver independence (`[tool.importlinter]` in pyproject.toml) |
| tools/guards archlint | `uv run python -m tools.guards` | A101 banned modules (`django.db.transaction` stays banned except `from django.db.transaction import TransactionManagementError` in `_django/transaction.py`), A102 Django sync connection/transaction (the only exceptions are the `swapped` context manager in `_django/connections.py`, imported only by `compile/scope.py`, and its read-only `configured_databases()`, imported only by `connection/lifecycle.py`), A103 blocking calls, `__import__` / `eval` / `exec` / `compile` / `__builtins__` and introspection attributes (dynamic imports are also A101: `importlib`, `pkgutil`, `runpy`, `pydoc`, `builtins`, `django.utils.module_loading`; so are the Django backend modules that import `MySQLdb`: `django.db.backends.mysql.base` / `.introspection` and the gis MySQL ones, in any import form; A102 rules live in `tools/guards/connlint.py`), A104 driver confinement (psycopg and every name Django modules re-export from it, as import, attribute or constant `getattr`: one verdict from the table in `tools/guards/reexports.py`; adapter helpers only via `_django/postgresql_adapters.py`, which only `drivers/postgresql` may import; Django's `mogrify` nowhere), A105 async in compile, A201 unledgered Django import, A202 stale ledger entry (incl. a `ported` entry whose `used_by` file is missing or lacks its `# Ported from django/<module path>.py` comment; `[[dependency]]` rows are exempt: nothing is imported, the drift test checks them), A203 private Django import outside `_django` |
| tools/guards testlint | same | T101 control flow, T201-T206 Given/When/Then, T301 sleep |
| tools/guards langlint | same | L101/L102 English-only, L201-L203 translations |
| ruff | `uv run ruff check` | style, bugbear, async pitfalls (ASYNC), banned `asgiref.sync` (TID251) |
| ty | `uv run ty check` | types |

`make guard` runs import-linter + guards; `make check` runs everything fast. The edit hook
(`tools/hooks/agent_hook.py`) runs guards + ruff on each edited file for every agent.

## Fixing a violation
Fix the code, never the guard. Guards have no inline suppressions by design. If you believe
a rule is wrong, stop and raise it with the human (and an ADR if it is architectural).

## Adding a new invariant
1. Prefer configuration: a new import-linter contract, a new entry in `BANNED_MODULES` /
   `BANNED_CALLS` in `tools/guards/archlint.py`.
2. Otherwise add an AST check with a new code (next free number in its family) and document it
   in the module docstring and in the relevant `.agents/rules/*.md`.
3. Add unit tests in `tests/unit/tools/` (GWT style: source string in, violation codes out),
   one passing and one failing case per code.
4. Run the full scan `uv run python -m tools.guards` - the repo must be clean.

## Adding a layer/subpackage
Create the package with a docstring stating its contract, add it to the `layers` contract in
`pyproject.toml`, update `docs/architecture.md` and `.agents/rules/architecture.md`.
