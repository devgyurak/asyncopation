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
