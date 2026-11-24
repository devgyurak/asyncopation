# Agentic development workflow

Translations: [Korean](workflow-KO.md)

This repository is built to be developed by coding agents (Claude Code, Codex and Pi, all
supported equally) with a human maintainer approving decisions. This document explains how the
pieces fit together.

## 1. One source, three agents

```
.agents/                      canonical, tool-neutral (edit here)
├── rules/*.md                path-scoped rules
├── agents/*.md               sub-agent definitions
└── skills/*/SKILL.md         workflows (Agent Skills standard)
        │
        │  make agents-sync  (tools/agentsync.py; CI fails on drift)
        ▼
.claude/rules, .claude/agents, .claude/skills      Claude Code
.codex/agents/*.toml                               Codex   (skills read from .agents/skills)
.pi/agents/*.md                                    Pi      (skills read from .agents/skills)
AGENTS.md index block                              Codex, Pi (and Claude via CLAUDE.md)
```

| Capability | Claude Code | Codex | Pi |
|---|---|---|---|
| Project instructions | `CLAUDE.md` -> `@AGENTS.md` | `AGENTS.md` | `AGENTS.md` |
| Rules | `.claude/rules/` (auto, path-scoped) | rules index in `AGENTS.md` | rules index in `AGENTS.md` |
| Skills | `.claude/skills/` | `.agents/skills/` | `.agents/skills/` |
| Sub-agents | `.claude/agents/` (Agent tool) | `.codex/agents/*.toml` (agent roles) | `.pi/agents/` via `.pi/extensions/subagent` (`agentScope: "project"`) |
| Edit hook | `.claude/settings.json` | `.codex/hooks.json` | `.pi/extensions/asyncopation-guard.ts` |

All three edit hooks call the same script, `tools/hooks/agent_hook.py`:
- **pre**: blocks edits to generated files (edit `.agents/` instead);
- **post**: runs `tools/guards` and `ruff check` on the edited files and feeds violations back
  to the agent immediately.

## 2. The feature loop

Every change follows the `feature-loop` skill:

```
 frame ──► research ──► design check ──► red tests ──► implement ──► refactor
 (plan      (django-     (architect;      (test-writer)  (hook feedback
  item)      researcher)  ADR if needed)                  on every edit)
                                                                 │
   close ◄── perf (hot paths) ◄── review-change ◄────────────────┘
 (plan ticks,  (perf-analyst)    (arch-guardian + code-reviewer
  docs, ledger,                   + test-reviewer, in parallel)
  translations)
```

Gates: guards clean -> tests red for the right reason -> `make check` + `make test-int` green
-> no open BLOCKER/MAJOR review findings -> no unexplained regression > 10% -> `make check-all`.

Docs move with the code: the `docs-sync` rule maps every kind of change to the documents that
must be updated in the same change, and `arch-guardian` reports misses as MAJOR findings.

Work is tracked on GitHub: one milestone per phase, one issue per roadmap item, labels for type
(`feature`, `perf`, `test`, `ci`, ...), layer (`area:*`), backend (`backend:*`) and process
(`needs-decision`, `blocked`). PRs close their issue with `Closes #N`.

Humans decide: accepting ADRs, changing guards/rules, updating benchmark baselines, releases.

Branches: work happens on `<prefix>/<kebab-summary>` branches cut from `develop` and is PR'd
into `develop`; `main` only receives release merges. Commit/PR titles use one prefix:
`ADD:` `FIX:` `REF:` `DEL:` `DOCS:` `UPT:` (the `commit` skill).

## 3. Sub-agents

| Agent | Access | Role in the loop |
|---|---|---|
| `architect` | read-only | design check, ADR review |
| `django-researcher` | read-only | Django call chains, hidden I/O, internals to port |
| `test-writer` | write | red phase: failing tests from the spec |
| `arch-guardian` | review | architecture review of the diff |
| `code-reviewer` | review | correctness review (async, transactions, drivers) |
| `test-reviewer` | review | test rules and adequacy |
| `perf-analyst` | review | benchmarks and interpretation |
| `translator` | write | keeps `-XX` translations in sync |

`review` agents may run commands (tests, benchmarks) but never edit files.

## 4. Mechanical enforcement

| Layer | Tool | When |
|---|---|---|
| Format, lint | ruff | edit hook, `make lint`, pre-commit, CI |
| Types | ty | `make typecheck`, CI |
| Layers | import-linter | `make guard`, pre-commit, CI |
| Architecture rules (A1xx/A2xx) | `tools/guards` | edit hook, `make guard`, pre-commit, CI |
| Test rules: GWT, zero control flow (T1xx-T3xx) | `tools/guards` | same |
| Language: English + translations (L1xx/L2xx) | `tools/guards` | same |
| Agent config drift | `tools/agentsync.py --check` | `make agents-check`, pre-commit, CI |
| Correctness | pytest differential tests on 3 backends | `make test-int`, CI matrix |
| End to end | uvicorn + httpx | `make test-e2e`, CI |
| Performance | micro + k6 harness | `make bench-micro` / `make bench`, CI (manual / nightly) |

Guards have no inline suppressions. Fix code, not guards; changing a guard needs the human.

## 5. Test rules in one paragraph

Every test has `# given` / `# when` / `# then` markers and contains no `for`, `while`, `if`,
`try`, `match`, comprehensions or conditional expressions. Variation comes from
`pytest.mark.parametrize` or Hypothesis, and helper logic lives in `tests/support/`.
Read paths are verified differentially against the sync ORM, on committed seed data
(`readonly_db` / `writable_db`), because async connections cannot see pytest-django's
per-test transaction.

## 6. Languages

English is the source language for code, comments, docs and agent files. Human-facing documents
listed in `docs/i18n.toml` ship translations named `<stem>-<LANG>.md` (e.g. `README-KO.md`).
Each translation pins the English revision it came from, so the guard flags it as stale when the
English text changes.

## 7. Day-to-day commands

```bash
make db-up && make check          # start of a session
make test-int ASYNCOPATION_DB=mysql
make agents-sync                  # after editing .agents/
uv run python -m tools.guards --changed
make bench-micro                  # hot-path change
```
