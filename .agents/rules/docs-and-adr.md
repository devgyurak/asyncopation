---
description: Design documents, ADRs and when a decision needs one
paths:
  - "docs/**"
---
# Docs and ADR rules

- `docs/design/plan.md` is the product plan/roadmap. `docs/architecture.md` is the current
  architecture. Keep both true: when code changes the picture, update them in the same change.
- Decisions that are expensive to reverse need an ADR in `docs/adr/NNNN-title.md`
  (template `0000-template.md`): connection strategy, public API shape, supported versions,
  driver behaviour differences, anything that deviates from the plan.
- ADR status flow: Proposed -> Accepted -> (Superseded by NNNN). Code that depends on a
  Proposed ADR must not be merged.
- Roadmap progress is tracked by ticking checklists in `docs/design/plan.md` (and its
  translations - see the language rule).
