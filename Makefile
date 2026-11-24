.DEFAULT_GOAL := help
SHELL := /bin/bash

ASYNCOPATION_DB ?= postgresql
SERVICES ?= postgres mysql mariadb
BENCH_ARGS ?=
PYTEST_ARGS ?=
export ASYNCOPATION_DB

.PHONY: help
help: ## Show this help
	@awk 'BEGIN {FS = ":.*##"} /^[a-zA-Z0-9_-]+:.*##/ {printf "  \033[36m%-16s\033[0m %s\n", $$1, $$2}' $(MAKEFILE_LIST)

# ------------------------------------------------------------------ setup
.PHONY: sync
sync: ## Install dependencies (dev + e2e groups)
	uv sync

.PHONY: hooks-install
hooks-install: ## Install git pre-commit hooks
	uvx pre-commit install

# ------------------------------------------------------------------ databases
.PHONY: db-up db-down db-reset db-psql db-mysql
db-up: ## Start test databases (SERVICES="postgres mysql mariadb")
	docker compose up -d --wait $(SERVICES)

db-down: ## Stop test databases (data is on tmpfs and is discarded)
	docker compose down

db-reset: db-down db-up ## Recreate test databases

db-psql: ## psql shell on the postgres container
	docker compose exec postgres psql -U asyncopation asyncopation

db-mysql: ## mysql shell on the mysql container
	docker compose exec mysql mysql -uasyncopation -pasyncopation asyncopation

# ------------------------------------------------------------------ quality
.PHONY: fmt lint typecheck guard agents-sync agents-check
fmt: ## Format and autofix (ruff)
	uv run ruff format .
	uv run ruff check --fix .

lint: ## ruff format --check + ruff check
	uv run ruff format --check .
	uv run ruff check .

typecheck: ## ty
	uv run ty check

guard: ## Architecture/test/language guards (import-linter + tools/guards)
	uv run lint-imports
	uv run python -m tools.guards

agents-sync: ## Regenerate .claude/.codex/.pi agent files from .agents/
	uv run python -m tools.agentsync

agents-check: ## Fail if generated agent files drifted from .agents/
	uv run python -m tools.agentsync --check

# ------------------------------------------------------------------ tests
.PHONY: test-unit test-int test-int-all test-e2e test
test-unit: ## Unit tests (no DB)
	uv run pytest tests/unit $(PYTEST_ARGS)

test-int: ## Integration tests on $ASYNCOPATION_DB (needs db-up)
	uv run pytest tests/integration $(PYTEST_ARGS)

test-int-all: ## Integration tests on postgresql, mysql and mariadb
	$(MAKE) test-int ASYNCOPATION_DB=postgresql
	$(MAKE) test-int ASYNCOPATION_DB=mysql
	$(MAKE) test-int ASYNCOPATION_DB=mariadb

test-e2e: ## End-to-end tests against uvicorn (needs db-up)
	uv run pytest tests/e2e $(PYTEST_ARGS)

test: test-unit test-int test-e2e ## Unit + integration + e2e on $ASYNCOPATION_DB

# ------------------------------------------------------------------ gates
.PHONY: check check-all
check: lint typecheck guard agents-check test-unit ## Fast gate (no DB)

check-all: check test-int test-e2e ## Full gate (needs db-up)

# ------------------------------------------------------------------ benchmarks
.PHONY: bench-micro bench bench-compare
bench-micro: ## In-process micro-benchmarks (needs db-up)
	uv run python -m bench.micro $(BENCH_ARGS)

bench: ## k6 load benchmark: WSGI vs ASGI core vs Asyncopation (needs db-up + docker)
	uv run python -m bench.run $(BENCH_ARGS)

bench-compare: ## Compare BASE=<json> HEAD=<json>
	uv run python -m bench.compare $(BASE) $(HEAD)
