---
name: test-db
description: Start, stop, reset and troubleshoot the docker-compose test databases (PostgreSQL 17, MySQL 8.4, MariaDB 11.8) and select the backend for tests. Use when tests cannot connect or a backend-specific run is needed.
---
# Test databases

| Backend | Service | Host port | `ASYNCOPATION_DB` |
|---|---|---|---|
| PostgreSQL 17 | `postgres` | 25432 | `postgresql` (default) |
| MySQL 8.4 | `mysql` | 23306 | `mysql` |
| MariaDB 11.8 | `mariadb` | 23307 | `mariadb` |

User/password/database: `asyncopation` / `asyncopation` / `asyncopation`. Data lives on tmpfs.

```bash
make db-up                         # all, waits for healthchecks
make db-up SERVICES=postgres       # one
make db-down                       # stop and wipe
make db-reset                      # down + up
make test-int ASYNCOPATION_DB=mariadb
make db-psql / make db-mysql       # interactive shells
```

pytest-django creates `test_asyncopation` and keeps it between runs (`--reuse-db`); use
`uv run pytest --create-db` after model changes.

MySQL/MariaDB sync side needs `mysqlclient` (`uv sync --group mysql-sync`, plus
`brew install mysql-client pkg-config` on macOS). With it installed, also run
`ASYNCOPATION_REQUIRE_MYSQLDB=1 uv run pytest tests/unit/django_drift tests/unit/compile` (the MySQL
wrapper drift suite and the "no `MySQLdb` loaded" subprocess checks; CI: integration and Django-main jobs).

## Troubleshooting
- `connection refused` -> `docker compose ps`; ports clash -> stop local DBs or change the
  host port in docker-compose.yml **and** harness/settings.py.
- `too many connections` during benchmarks -> `max_connections` is 500; check for leaked pools.
- Stale schema -> `uv run pytest --create-db`.
