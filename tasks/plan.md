# Implementation Plan: Strengthen the FPL model database

Work: conv-a12bcb. Canonical task tracker: hive/tasks.json. Research: docs/research/database-strengthening.md.

## Evidence and scope
The development copy has 293/449 played FPL players linked to FotMob, covering 70.3% of minutes. The research candidate rule reaches an estimated 95.4%, but the sampled pairs do not establish precision. Production contents remain unknown. Improve identity coverage and observability; retain DuckDB and existing data sources.

## Ordered tasks and owners
- FAA-1: Research, Pablo, complete.
- FAA-2: Evidence-based planning, god, complete.
- FAA-4 (S1): Read-only link coverage report and missing-name diagnostic, Pablo. Depends on FAA-1/2.
- FAA-5 (S2a): Conservative candidate generation, Pablo. Depends on S1.
- FAA-6 (S2b): Reversible opt-in linking and cutoff-safe model consumption, Pablo. Depends on S2a.
- FAA-7 (S3): Scratch-copy production verification, Wegz (agent oscar-mvpkza94), parallel with implementation; rerun new audit report after S1 exists.
- FAA-3: Independent code-review-and-quality audit, Wegz, after S1/S2a/S2b. Route required findings to Pablo and re-review before acceptance.

Task cards contain acceptance criteria, verification and likely file scope. Pablo should keep each slice focused (roughly 3-5 files), report if further splitting is needed and preserve existing changes.

## Design constraints
Appearance similarity proposes identity; it does not prove it. Require canonical fixture and same-team evidence, one-to-one uniqueness in both directions, no conflicting established IDs, distinct evidence matches and conservative rejection of ambiguity. Test full-minute teammates, opposing players, equal-minute substitutes, duplicates and contradictions. Inspect all accepted pairs on the dev copy, record evidence, and prefer lower recall to a false identity.

Links must be explicitly opt-in, auditable, reversible and transactional. Preserve raw source rows. Past model/replay cutoffs must not see links inferred or recorded from future evidence; identity being time-invariant does not waive decision-time constraints. Future-only evidence must leave historical projections unchanged. No automatic application to the production database in this scope.

## Checkpoints and verification
After S1: reproduce baseline and unchanged database hash, including legacy schema support. After S2a: adversarial tests and full candidate evidence review before persistence. After S2b: exact undo, repeatability, rollback, correct model rates and no duplicate rows or future leakage; before/after coverage measured on a copy. Run focused tests and required full checks from repository CI (including coverage gate). An estimated 95% coverage is not an acceptance quota; report safe achieved coverage and rejected cases honestly.

Wegz reviews correctness, readability, architecture, security and performance using ~/.claude/skills/code-review-and-quality/SKILL.md and the leakage checklist in .claude/agents/leakage-reviewer.md. Include a safe isolated mutation test with restoration, severity-ranked file/line findings, commands/results, and explicit verdict. Required findings return to Pablo.

## Boundaries and unknowns
Preserve existing work, dry-run defaults, source transactions and eligibility rules. Use scratch database copies for changes. No paid services, destructive repairs, live FPL actions or remote publishing. Production access may be unavailable; report the limitation without claiming local results apply to production. User authorized autonomous planning and execution; no extra approval checkpoint is needed for this bounded work.

## Audit correction phase
Wegz report docs/research/database-link-audit.md requests changes. All findings 1-7 are required; finding 8 is also in scope because retaining the undo journal is part of reversible linking. Pablo owns three sequential slices: temporal/observation integrity; active-link revalidation and conflict exclusion; season/reference/journal integrity. Tracker cards carry acceptance tests. Temporal correctness takes priority over reproducing earlier coverage or projection counts. Recompute results under corrected semantics and label exploratory versus validated evidence accurately. Do not claim exact file undo when additive schema/journal rows persist. Required full checks follow fixes, then Wegz independently re-reviews before acceptance.
