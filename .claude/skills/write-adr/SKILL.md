---
name: write-adr
description: Write an Architecture Decision Record for an expensive-to-reverse decision (connection strategy, public API, supported versions, driver behaviour). Use when architect says an ADR is needed or a change deviates from the plan.
---
# Write an ADR

1. Next number: `ls docs/adr` -> `NNNN-kebab-title.md`, copy `docs/adr/0000-template.md`.
2. Fill: Context (facts, links to plan sections, Django source lines), Options (at least two,
   with consequences each), Decision, Consequences (including what becomes harder), Validation
   (which tests/benchmarks prove it).
3. Status `Proposed`. Ask **architect** for a review and include its verdict.
4. Stop and ask the human to accept. Only the human changes status to `Accepted`.
5. Link the ADR from `docs/architecture.md` and from code that depends on it
   (`# See ADR-0001`).
English only; ADRs are not translated.

# Amend an ADR
Use this instead of a new ADR only when the chosen Option stays the same and no Validation item
becomes invalid (detail or scope refinement). Otherwise write a superseding ADR (steps above).
An amendment may add or tighten Validation items but never drop or contradict one.
1. Ask **architect** to review the amendment and wait for a real verdict.
2. Add one header line (after `Architect review`): `- Amended: <date> (#N, PR #M) - <every
   changed item>; approved by: <maintainer>; architect verdict: <verdict>`. One verdict per line,
   never `PENDING` at merge. Write the approval as "to be confirmed in the PR review" until the
   maintainer gives it, then as `maintainer (@login), <date> in the PR #M review`: an agent cannot
   self-attest the maintainer's approval. Status stays `Accepted`.
3. Mark every changed or contradicted Decision/Validation sentence, code block and Option pro/con
   inline with `*Amended <date> (#N, PR #M): ...*`; detail may live in an "Implementation note".
4. Update `docs/architecture.md` and the docs-sync targets as for any ADR change.

# Supersede part of an ADR
Use this when a new ADR replaces some Decisions of an Accepted ADR and the others still hold (an
amendment is not allowed because a Validation item becomes invalid or the direction changes).
1. Write the new ADR (steps above) with a `- Supersedes in part:` header naming the replaced
   Decisions, Consequences bullets and Validation items; its Validation replaces every invalidated
   item explicitly.
2. In the old ADR keep `Status: Accepted` and add one header line `- Partially superseded: <date>
   (#N, PR #M) - <every superseded item> are superseded by [ADR-XXXX](...); <what stays
   Accepted>`.
3. Mark every superseded Decision, Consequences bullet and Validation item inline with
   `*Superseded <date> by ADR-XXXX (#N, PR #M): ...*` (`*Superseded in part ...*` for part of one);
   a still-valid item whose meaning narrows gets `*Clarified <date> by ADR-XXXX (#N, PR #M): ...*`.
4. Ask **architect** to review both, and merge the marks only with the new ADR `Accepted` by the
   maintainer in that PR's review.
