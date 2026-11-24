---
name: feature-loop
description: End-to-end loop for implementing one roadmap item (spec -> design check -> failing tests -> implementation -> guards -> multi-agent review -> perf -> docs). Use whenever starting work on a feature, phase checklist item or bug fix in src/.
---
# Feature loop

One iteration = one roadmap checklist item (or one bug). Keep iterations small enough to
review in one sitting. Each step names its gate; do not move on until the gate passes.

## 0. Frame
- Pick the GitHub issue from the current `Phase N` milestone (`gh issue list --milestone "Phase 1 - First working read path"`)
  and read it with its dependencies; skip issues labelled `blocked` or `needs-decision`.
- Locate the item in `docs/design/plan.md` (phase + checklist line). Write a 3-5 line spec:
  public API, behaviour, backends, out of scope.
- Gate: spec exists (in the PR description draft or the conversation).

## 1. Research (optional, for anything touching Django internals)
- Delegate to **django-researcher**: call chain, I/O points, reusable pure helpers.
- Gate: you can name every I/O point the feature touches.

## 2. Design check
- Small, single-layer change: self-check against `.agents/rules/architecture.md`.
- Cross-layer change, new public API, or anything in connection/transaction: ask
  **architect** for a review; if it says "ADR needed", run the `write-adr` skill and stop until
  the ADR is Accepted by the human.
- Gate: layer placement is clear; no Proposed ADR is a dependency.

## 3. Red - tests first
- Delegate to **test-writer** (or follow `write-tests` yourself). Differential tests for read
  paths, unit tests for pure logic, an e2e scenario when the feature is user-visible through
  the harness site.
- Gate: `uv run python -m tools.guards --changed` clean; new tests fail for the right reason.

## 4. Green - implement
- Smallest change that passes. Follow `port-django-internals` when porting.
- The edit hook runs guards + ruff on every file you touch; fix what it reports immediately.
- Gate: `make check` passes and `make test-int` passes on PostgreSQL. Run MySQL/MariaDB
  (`make test-int ASYNCOPATION_DB=mysql`) when drivers, types or SQL differ per backend.

## 5. Refactor
- Remove duplication, keep ports structurally close to upstream, update docstrings (English).

## 6. Review
- Run the `review-change` skill (arch-guardian + code-reviewer + test-reviewer in parallel).
- Gate: zero BLOCKER/MAJOR findings left open (fixed, or rebutted with evidence).

## 7. Performance (hot paths: query/, connection/, drivers/, fetch/, prefetch/)
- `make bench-micro`; for user-visible paths delegate to **perf-analyst** for a k6 run.
- Gate: no unexplained regression > 10% vs baseline.

## 8. Close
- Walk the `docs-sync` rule table for every changed file and update each listed document.
- Tick the checklist line in `docs/design/plan.md` (and `plan-KO.md`); update
  `docs/architecture.md` if the picture changed; record Django internals in
  `docs/django-api-ledger.toml`. The PR body says `Closes #N`.
- If an English doc with translations changed, update them (`translate-docs` / **translator**).
- Commit following the `commit` skill (`ADD: alist() on aobjects for PostgreSQL`), on a
  `<prefix>/<kebab-summary>` branch cut from `develop`; PR into `develop`.
- Gate: `make check-all` green.
