# Review: Gate 0 export gap matrix (FAA-33)

Reviewer: Wegz (wegz-mvsc3fox), 2026-10-29. Reviewed: `artifacts/database-gate0/export-gap-matrix.md`,
SHA-256 `dcc057e1509a6b0c1a68384420d0497e5564c0b2af397d3ec52293cc4491c915`.

## How I checked

Read-only: git object reads, saved archive evidence, and source reading. No extraction, diagnostics, tests, code
edits or production actions.
- `git show <rev>:src/fpl_agent/db/schema.py` for `51f0def`, `b0448c7`, `90bd56c`, `de3ecd3`, `b8f05e9` and
  `c6a0c5f`, plus the working tree. Declared tables were counted by a parser in my scratchpad.
- `git show 51f0def:` for `db/database.py` (`export_parquet`) and `scripts/data_publish.sh`.
- `artifacts/FAA-7-production/r20261028/`: `db-eligibility.json`, `source-health.json`, `README.md`.
- Working-tree `pipeline.py` (only to check two claims).

## Verdict: APPROVE the analysis, with three corrections and conditions on the remediation plan

The root cause, the table classification and the production vs development distinction hold up against the
evidence. The corrections below change no conclusion. The conditions (G1-G6) define a safe minimum exit for Gate 0
and must be met before the remediation in §4 is executed.

## Claims verified

| Claim | Result |
|---|---|
| Production runs `51f0def`; it is the tip of master and an ancestor of HEAD | **Confirmed.** `git log origin/master -1` = `51f0def`; `merge-base --is-ancestor 51f0def HEAD` is true. The `head_sha` of the plan run is in the saved `workflow-runs-20261028.json` (not re-fetched). |
| Declared tables: 23 / 32 / 34 | **Confirmed exactly.** 51f0def has 23. c6a0c5f has 32 (adds fpl_deadlines, match_odds_log, migration_log, news_evidence, player_codes, repair_log, runs, scout_cache, scout_calls). The working tree has 34 (adds source_attempts, player_links). |
| The production exporter skips empty tables and writes no manifest | **Confirmed.** `51f0def:db/database.py:280` `if n:` before `COPY … TO parquet`. No manifest writer. |
| The production publisher overwrites a single asset | **Confirmed.** `51f0def:scripts/data_publish.sh:8,11`: `tar` then `gh release upload data … --clobber`. |
| GWs 6-8 are rejected only for the missing official deadline | **Confirmed.** `db-eligibility.json`: GW6/7/8 have exactly one reason ("deadline approximate…") with coverage 1.0 / 0.9985 / 0.9985. GW5 also lacks snapshot coverage (0 of 667). GWs 1-5 are unavailable. |
| Source health is "never" for the FPL sources | **Confirmed.** `source-health.json`: bootstrap, fixtures and players are all `never`, overall `degraded`. |
| Production link coverage is 83.36%; development is 97.40% | **Confirmed and correctly separated.** README: 131,188/157,372 minutes, all linked by name, 0 by evidence, no `player_links` in the release. The FAA-3 caveat still applies: identity precision is not established (Beto and Costinha unconfirmed). |

## Corrections

**K1. Commit attribution (§1 L11, table column 2).** "9 in commit `c6a0c5f`" is not exact. The table count goes
23 → **28 at `90bd56c`** → 32 at `c6a0c5f` (`b0448c7` still has 23). Write "added on the task branch after `51f0def`
(commits `90bd56c` to `c6a0c5f`), not merged to master". The root cause is unchanged.

**K2. The FPL-token dependency is overstated (§4 step 3: "Without it, step 2 may record nothing").** The bootstrap,
fixtures and player data are public. In the working-tree `pipeline.py:98-128`, bootstrap is the first critical,
unauthenticated pull. So recording deadlines and source attempts should not depend on the account token, unless the
workflow aborts before ingestion when the token check fails. That ordering has not been verified. Mark this as
inference and check which workflow (data or plan) records deadlines and whether a token failure stops it first.

**K3. A new silent-stop risk from code reading (inference).** In the working-tree `pipeline.py:122-123`, when
automatic repairs are disabled (the default, L63-65) and a repair is needed, **the update loads nothing**. If the
hardened release ships this behaviour, one blocked repair could stop deadline and attempt recording without anyone
noticing. That is exactly the prospective data Gate 0 is about. Add it to §5 and to the verification list (G4).

## Classification review

- **A (provenance):**
  - `fpl_deadlines` and `source_attempts`: correct, and these are the blockers.
  - `match_odds_log`: blocks only an odds-using evaluation (current eligibility reports `pre_cutoff_odds_matches: 0`
    and does not reject on odds). Recording is prospective-only and cheap, so ship it in the same release as the
    matrix proposes. Label it "A (conditional)".
- **B:** `player_links` and `player_codes`: correct. Both affect coverage and precision; neither gates prospective
  recording.
- **C (`migration_log`, `repair_log`):** "not needed until R1 runs" is right only if the release keeps R1 relabel and
  R2 repairs **disabled**. If any R1/R2 write path is active in the release, its journal table is a release
  prerequisite. Otherwise a production write could not be undone (G2).
- **D/E:** correct. Exporting the empty tables is enough.

## Conditions for a safe minimum Gate 0 exit

- **G1. A scoped release, not the working tree.**
  - The proposed release must be a scoped commit containing only the export/publish hardening and the provenance
    tables and writers. The uncommitted tree also contains unreviewed `model/features.py` and `players.py` changes
    (FAA-15), the dashboard work and more; all of that stays out.
  - The release needs its own independent review against the exact diff hash.
  - The DB08 gate covered isolated local development only. It does not cover a production release, and the r5
    review left Linux archive execution unverified, so CI on Linux is required evidence.
- **G2. R1/R2 write paths stated.**
  - The release states which R1/R2 write paths are active.
  - Any active path ships its journal table.
  - Repairs stay opt-in.
- **G3. Migration rehearsal before publishing.**
  - Rehearse the 23→34-table migration on a copy restored from the pinned `r20261028` archive
    (`sha256:3dc27152…c69f`).
  - Show that restore works for both the old asset (no manifest) and the new one (manifest).
  - Show rollback: the previous asset is retained as a dated copy, and restoring it still works with the new code.
- **G4. Prospective recording proven live before the next deadline.** The first verified export (pinned by asset
  id and sha256) must show:
  - a manifest that verifies;
  - all 34 tables present;
  - `fpl_deadlines` holding an observation of the upcoming gameweek's official deadline, taken before that deadline;
  - `source_attempts` non-empty, and the FPL sources no longer `never`;
  - no run that "loaded nothing" (K3).
  The next deadline must be confirmed from the official source at that time, not from the M00 note.
- **G5. Transition window.**
  - Until master carries the new exporter, every 2-hour plan run keeps overwriting the asset with a manifest-less
    export, and each gameweek whose deadline passes stays permanently non-eligible.
  - Keep the `r20261028` local archive and its digest as the fallback.
  - State in writing which gameweek is the first that can become eligible.
- **G6. Separate decisions.**
  - The identity apply (`player_links`, `player_codes`) needs its own authorised apply with a rollback batch.
  - Until then, production quotes 83.36% and development 97.40%, never mixed.
  - GWs 1-8 can never become eligible; no claim may imply otherwise.

## Limits

- Workflow run data, asset metadata and archive hashes were taken from saved files, not re-fetched.
- `pipeline.py` was read only for K2/K3. Those findings are inferences from code, not observed production runs.
- I did not review the hardening diff itself. That review is G1.
