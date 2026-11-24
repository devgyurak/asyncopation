<!-- Title: `<PREFIX>: <summary>` with one of ADD / FIX / REF / DEL / DOCS / UPT (see the `commit` skill).
     Work PRs target `develop`; only release PRs target `main`. -->

## What

Closes #

<!-- Roadmap item (docs/design/plan.md phase + checklist line) or bug. -->

## How

<!-- Layers touched, Django internals reused/ported (ledger entries), ADRs relied on. -->

## Verification

- [ ] `make check` green
- [ ] `make test-int` on postgresql / mysql / mariadb (or CI matrix)
- [ ] `make test-e2e` (user-visible scenarios)
- [ ] `review-change`: no open BLOCKER/MAJOR (paste the consolidated table)
- [ ] hot path changed? `make bench-micro` before/after attached
- [ ] docs updated per the `docs-sync` rule (plan checklist, architecture, ledger, skills, translations)
