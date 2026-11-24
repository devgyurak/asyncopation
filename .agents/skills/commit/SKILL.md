---
name: commit
description: Commit message, PR title and branch name conventions (ADD/FIX/REF/UPT/DEL prefixes) and the main/develop branch flow. Use right before committing or opening a PR, or when asked "commit this", "open a PR", "which prefix?".
---
# Commits, PRs and branch names

> Commit **only when the user asks**. This skill is about *how* to write it, not *when*.

The commit summary, the PR title and the branch name share **one prefix**. One PR carries one
kind of change, so one prefix is always enough.

```
<PREFIX>: <summary>

<body - explain WHY>
```

## 1. Look at what actually changed

Pick the prefix from the diff, never from memory.

```bash
git status --short
git diff --stat
git diff            # unstaged
git diff --staged   # staged
```

## 2. Pick exactly one prefix

| Prefix | Use when | Example |
|---|---|---|
| `ADD:` | Something new: feature, API, test, workflow, guard, harness, new dependency, new document or skill | `ADD: alist() on aobjects for PostgreSQL` |
| `FIX:` | Behaviour was wrong and now matches the intent | `FIX: release pooled connection when alist() is cancelled` |
| `REF:` | Structure changes, **observable behaviour is identical** | `REF: move converter lookup into query.iterables` |
| `UPT:` | Updating something that already exists without it being a fix or a refactor: dependency version bumps, `uv.lock` refreshes, content updates to existing docs/rules/skills, baseline refreshes | `UPT: django 6.1.4 and re-verified ledger` |
| `DEL:` | Removing a feature, file, API or dead code | `DEL: unused sync fallback in drivers.mysql` |

Upper case, colon, one space: `ADD: ...`, never `Add:` or `ADD :`.

### When the boundary is unclear, decide in this order

1. **Did observable behaviour change?** No -> `REF:`. New behaviour -> `ADD:`. Wrong
   behaviour corrected -> `FIX:`. A `REF:` with a user-visible effect has the wrong prefix.
2. **Upstream Django changed and we adapted** (the `django-main` canary broke) -> `FIX:`;
   mention the Django commit/version and the ledger entries in the body.
3. **Dependencies:** new package in `pyproject.toml` -> `ADD:`; version bump or `uv.lock`
   only -> `UPT:`; removed package -> `DEL:`.
4. **Code + the docs that describe it** -> the code's prefix (`ADD:`/`FIX:`/`REF:`). Docs
   ride along with the change they document.
5. **Docs only:** a new document, ADR, skill, rule or sub-agent -> `ADD:`; changing an existing
   one (including translations) -> `UPT:`; removing one -> `DEL:`.
   Agent configuration (`.agents/`, generated `.claude/`, `.codex/`, `.pi/`) counts as docs.
6. **Tests only:** new tests -> `ADD:`; restructuring existing tests -> `REF:`.
7. **CI, guards, lint config:** new workflow/rule/guard -> `ADD:`; behaviour change of an existing
   one -> `UPT:` (or `FIX:` if it was wrong); tidy-up -> `REF:`; removal -> `DEL:`.
8. **Two prefixes both fit?** Split the commit. "Add a feature and refactor on the way" is two
   commits.

## 3. Summary line

- **English**, imperative or noun phrase, no trailing period (see the language rule).
- About 50-72 characters including the prefix.
- Describe the **change**, not the files.

```
good:  FIX: release pooled connection when alist() is cancelled
bad:   FIX: update connection.py
bad:   FIX: bug fix
```

## 4. Body - the why

The diff already says *what*. The body says **why**: the failing scenario, the Django source
line or ADR that justified the change, the benchmark numbers for a perf change.

This project has deliberately unusual code (ported Django internals, compile-only connection,
no thread hops). If the reason is not written down, the next agent will "clean it up" and
silently regress it. The body is **required** when the commit:
- ports or depends on Django internals (name the upstream function and version);
- touches connection ownership, transactions or cancellation;
- changes a guard, rule or benchmark baseline.

Agents append the attribution trailer their harness requires.

## 5. Branches and flow

```
main      released / releasable history. Receives PRs from develop only (merge commit).
develop   integration branch. All work PRs target develop (squash merge).
<prefix>/<kebab-summary>   work branches cut from develop
```

Branch name: lower-case prefix + kebab summary.

```
add/aobjects-alist
fix/cancelled-alist-leak
ref/iterables-converters
upt/django-6-1-4
del/mysql-sync-fallback
```

## 6. PR title

Same as the commit: `ADD: alist() on aobjects for PostgreSQL`. With squash merges on
`develop` the PR title becomes the history line, so it must follow the same rules. Fill in
`.github/pull_request_template.md` (verification checklist + review table).

## 7. Before you commit

- [ ] Prefix matches the diff (no `REF:` with behaviour change).
- [ ] One kind of change per commit.
- [ ] Summary describes the change; body explains why (required cases above).
- [ ] `make check` green; `make test-int` when `src/` or `tests/integration` changed.
- [ ] `review-change` done for changes in `src/` or `tests/`.
- [ ] No secrets, no `bench/results/`, no `.claude/settings.local.json`.
- [ ] Translations re-stamped if an English source with translations changed.
