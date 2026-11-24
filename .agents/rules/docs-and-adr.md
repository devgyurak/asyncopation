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
- **Amending an ADR.** An ADR may be amended in place (status stays Accepted) **only if** the
  chosen Option is unchanged and no Validation item becomes invalid; then it is a refinement of
  detail or scope. Otherwise (direction changed, trade-off reversed, a Validation item no longer
  holds) write a new ADR that supersedes it. An amendment may add or tighten Validation items;
  it must never drop or contradict one. Amending requires:
  - one header line `- Amended: <date> (#N, PR #M) - <every changed item>; approved by:
    <maintainer>; architect verdict: <verdict>`. The verdict is a real verdict from the
    `architect` sub-agent, never `PENDING` at merge, one verdict per Amended line. The
    maintainer's approval is recorded as "to be confirmed in the PR review" until the maintainer
    gives it, then as `maintainer (@login), <date> in the PR #M review` - an agent cannot
    self-attest it;
  - an inline mark `*Amended <date> (#N, PR #M): ...*` on **every** Decision or Validation
    sentence, code block and Option pro/con that is changed or contradicted (an "Implementation
    note" section may hold detail, but the affected places still carry the mark).
- Roadmap progress is tracked by ticking checklists in `docs/design/plan.md` (and its
  translations - see the language rule).
