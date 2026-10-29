# Gate 0 — export gap matrix (FAA-32)

By Zaza (`zaza-mvsc3unw`), 2026-10-28. Read-only: built from the verified build in
`artifacts/FAA-7-production/r20261028/` (asset 685637934, `sha256:3dc27152…c69f`) plus git and
code reading. No extraction, diagnostics rerun, code edit or production action.
**F** = fact (cited), **I** = inference.

## 1. Root cause in one line
**F:** production runs **master `51f0def`**. Its exporter writes only non-empty tables, writes no
manifest, and overwrites a single rolling asset. **11 of the 13 "missing" tables do not exist in
production code at all.** They were added later, on `task/phase1-dashboard`: 9 in commit `c6a0c5f`
(not merged to master) and 2 only in uncommitted working-tree changes. The other 2 exist in
production but were empty, so they were skipped.

Evidence:
- `git branch -a --contains 51f0def` → master, origin/master and the task branch, so 51f0def is
  master's tip and an ancestor of HEAD `c6a0c5f`.
- The `plan` run 40727258853 used `head_sha 51f0defa3642` (`r20261028/workflow-runs-20261028.json`).
- Declared tables, compared with the 21 exported files (computed by loading each revision's `schema.py`):
  | Revision | Declared | Declared but not exported |
  | --- | --- | --- |
  | `51f0def` (production) | 23 | transfers, league_translation |
  | `c6a0c5f` (HEAD, committed) | 32 | + match_odds_log, scout_cache, scout_calls, runs, news_evidence, player_codes, migration_log, repair_log, fpl_deadlines |
  | working tree (uncommitted) | 34 | + source_attempts, player_links |
- Production exporter (`git show 51f0def:src/fpl_agent/db/database.py`, `export_parquet`): `if n:` → a
  table with zero rows is not written. No manifest.
- Production publisher (`git show 51f0def:scripts/data_publish.sh`): `tar` of the folder →
  `gh release upload data … --clobber`. No verification, no manifest, no dated copy. The `plan`
  workflow (`plan.yml` line 117, cron `10 */2 * * *`, every 2 h) and the `data` workflow (`data.yml`
  line 51, daily and weekly) both publish the same asset. They share concurrency group `data`
  (non-dry runs), so they run one at a time. **F:** the asset can be replaced as often as every
  2 hours, which explains the 2026-10-26 → 2026-10-28 change. (Whether every plan run reaches the
  publish step was not checked.)

## 2. The 13 tables
Class key: **A** = Gate 0 provenance (blocks point-in-time evidence), **B** = identity/coverage quality,
**C** = audit/rollback journal, **D** = operational/feature (not needed for model evidence),
**E** = expected empty.

| Table | In prod code? | Why absent (F) | What it holds (schema comment) | Class | Needed before model work? |
| --- | --- | --- | --- | --- | --- |
| `fpl_deadlines` | no (c6a0c5f) | not declared in production | every observed official deadline, versioned by `observed_at`; prospective only, "earlier deadlines were never stored" | **A** | **Yes, the key blocker.** Eligibility rejects GWs 6–8 *only* because "no official deadline recorded before it passed"; their snapshot coverage is 1.0 / 0.9985 / 0.9985 (`db-eligibility.json`). |
| `source_attempts` | no (working tree) | not declared | outcome of every source attempt, failures included | **A** | **Yes.** Without it, source-health reports FPL sources as "never" and cannot judge freshness or failures. |
| `match_odds_log` | no (c6a0c5f) | not declared | append-only odds history; "a past decision's prices can only be reconstructed from here (prospectively)" | **A** | **Yes**, for any odds-using model evaluated point-in-time. Prospective only. |
| `player_links` | no (working tree) | not declared | reviewed FotMob→FPL evidence links with `valid_from` and rollback | **B** | **Yes**, for coverage: production has 83.36% of minutes linked against 97.40% on the reviewed development copy. It also needs an authorised apply after deploy. |
| `player_codes` | no (c6a0c5f) | not declared | FPL `code` per canonical player (identity across seasons, R1) | **B** | Yes (identity precision). It is filled by `fpl_identity.py`. |
| `migration_log` | no (c6a0c5f) | not declared | journal of the reversible R1 relabel | **C** | Needed for rollback of R1 once R1 runs in production. Not a model input. |
| `repair_log` | no (c6a0c5f) | not declared | before/after images of automatic repairs and quarantines (R2) | **C** | Same as above, for audit and rollback. |
| `runs` | no (c6a0c5f) | not declared | per-run stages, reasons, delivery | **D** | No. Useful for operations. |
| `news_evidence` | no (c6a0c5f) | not declared | observed news payloads | **D** (I: possible future input) | No, unless a model uses news. |
| `scout_cache` | no (c6a0c5f) | not declared | cached scout answers | **D** | No. |
| `scout_calls` | no (c6a0c5f) | not declared | scout call usage/outcome | **D** | No. |
| `transfers` | **yes** | empty → skipped by `if n:` | player club transfers | **E** (I: no production writer found by grep) | No. Exporting it empty is enough. |
| `league_translation` | **yes** | empty → skipped (I: `model/translation.py` writes it, but it was empty at export) | cross-competition stat translation factors | **E/D** | Not for Gate 0. Check whether production should be computing it. |

## 3. Manifest absence
- **F:** the archive holds only 21 `.parquet` files and no manifest (`r20261028/parquet-hashes-before.json`).
  Production code (51f0def) has no manifest writer.
- **F:** the uncommitted working tree adds one. `export_parquet` writes every table (empty ones
  too), stages it, writes a manifest of row counts, columns and checksums, and keeps `.prev-*`
  folders. `scripts/data_publish.sh` runs `verify-export` first, uploads a dated
  `football-db-<stamp>.tar.zst` beside the rolling asset, and refuses to run anywhere but Linux with
  zstd. None of this is committed or deployed.
- **Consequence (F):** at present, a consumer cannot prove an export is complete, cannot tell an
  empty table from a dropped one, and cannot roll back. The previous asset is destroyed by `--clobber`.

## 4. Minimum remediation before model work
Ordered. Owners follow the charter: Pablo/Nabil implement, Wegz reviews, god accepts, Zaza integrates.

1. **Ship the export/publish hardening to master** (manifest, export every declared table, dated
   archives, `verify-export` gate). It is already written; it needs a scoped commit, CI on Linux,
   Wegz review and god's release authorisation.
2. **Ship provenance recording in the same release:** the `fpl_deadlines`, `source_attempts` and
   `match_odds_log` tables and their writers. **Time-critical (I):** all three are *prospective only*.
   Every gameweek that passes before deployment stays permanently "reconstructed" (as GWs 6–8 are),
   even with full snapshot coverage. The next deadline is not verified here; the TASK.md M00 note
   mentions 31 Oct for GW9 context only.
3. **Fix the FPL token** so the plan run can sign in. The `token-check` scheduled runs failed at
   09:59Z and 18:28Z on 2026-10-28. This is a human/god account action, and I took none. Without it,
   step 2 may record nothing.
4. **After deploy, verify the first new export** (Zaza, read-only, as in r20261028). Check: manifest
   present and verified; all 34 tables present (empty allowed); `fpl_deadlines` holds an observation
   for the upcoming GW before its deadline; `source_attempts` is non-empty; source-health is no longer
   "never".
5. **Identity (separate, reviewed):** apply the FAA-3 reviewed `player_links` and R1 `player_codes`
   to production only through an explicitly authorised `db-links --apply` (or equivalent) with a
   rollback batch id. Until then, quote **83.36%** as the production figure.
6. **Always pin by digest:** the rolling asset changes with every plan or data run. Every
   verification must record the asset id and the sha256 at the time of the check.

Not required for Gate 0: `runs`, `news_evidence`, `scout_*`, `transfers`, `league_translation` (export empty is fine).

## 5. Open points and limits
- The working-tree export and schema changes are uncommitted and have not been reviewed as a
  release; the 1,524-line diff of `db/` and scripts since 51f0def is not split into scoped changes.
- Migration of the production DB to the 34-table schema has not been rehearsed against the r20261028 build.
  Proposed next bounded task: a scratch rehearsal on a copy, after approval.
- Historical GWs 1–8 cannot be made eligible by any remediation. Official deadlines were never stored
  before they passed.

---

## Addendum (2026-10-28 ~21:15Z): corrections K1–K3 from Wegz's review
These **supersede** the conflicting statements above. All are read-only and come from git
history and code. **F** = read directly from code or git; **I** = inferred from code (not run or
observed in production).

### K1: exact commit attribution (corrects §1 and the §2 "In prod code?" column)
**F** (each revision's `schema.py` loaded and its `TABLES` keys compared, in commit order):
| Revision | Tables | Added at this revision |
| --- | --- | --- |
| `51f0def` (master, production) | 23 | — |
| `80a266e` → `b0448c7` (merge) | 22 → 23 | `lessons` (merge; no change from 51f0def's set) |
| `90bd56c` | **28** | `match_odds_log`, `news_evidence`, `runs`, `scout_cache`, `scout_calls` |
| `de3ecd3`, `b8f05e9` | 28 | — |
| `c6a0c5f` (HEAD) | **32** | `fpl_deadlines`, `migration_log`, `player_codes`, `repair_log` |
| working tree (uncommitted) | **34** | `player_links`, `source_attempts` |
§1 earlier said "9 in c6a0c5f". **Correct: 5 in 90bd56c, 4 in c6a0c5f, 2 uncommitted.** None of
these commits are on master.

### K2: where deadline and attempt recording runs, and the token (corrects §4 step 3)
- **F:** official deadlines are written by `db/gameweeks.log_deadlines`, called from
  `db/ingest_fpl.load_bootstrap` (line 72) using the `events` of the public `bootstrap-static/`
  response. `data/fpl_client.py` says: "Read-only client for FPL's public endpoints. **No auth**."
- **F:** `source_attempts` is written by `pipeline.record_attempts`, which `_finish` calls on
  **every** `db-update` / backfill exit, including failures.
- **F:** both run inside `fpl-agent db-update` (`pipeline.run_update`). That command runs in **both**
  workflows: `data.yml` lines 45/48 (daily and weekly) and `plan.yml` line 91 (step `refresh`, every 2 h).
- **F:** `token-check.yml` is a separate workflow. It shares only concurrency group `fpl-token` with
  the plan's sign-in lease. Neither `data.yml` nor `plan.yml`'s `refresh` step has a `needs:` or `if:`
  on token-check. The plan **job** is gated on `gate.fresh` and `vars.FPL_ENTRY_ID`, not on the token.
- **Correction:** the FPL token is **not** a prerequisite for recording deadlines or attempts. The
  failing token-check affects only the signed-in plan and team steps. §4 step 3 is downgraded to a
  separate operations item. It is not Gate 0 provenance.

### K3: the default blocked-repair path that loads nothing (adds a §4 risk and a detection step)
- **F** (`pipeline.py` lines 63–65, 122–124): `UpdateOptions.repairs` defaults to `False`. In that case
  `blocked_writes(db, repair, "repair")` (`quality.py` line 387) runs the repair on an in-memory copy.
  If it would change any table, that is an error. The update then **returns `BAD_DATA` (exit 3)
  before `load_fpl`**: no players, no snapshot and **no deadline** are loaded. The same applies to
  backfill (lines 367–369). The quarantine step (line 195) also uses `blocked_writes`, but later, after
  loading.
- **F:** neither workflow passes `--repairs` (data.yml 45–51, plan.yml 91–92), so this default applies
  in CI.
- **F:** on that path, `_finish` still records `source_attempts` with `exit_code` 3. It skips the export
  (`export_parquet` runs only when `code == OK`) and sends `notify.send(...)` (`alert` defaults to True).
  `plan.yml`'s "Publish database" needs `steps.refresh.outcome == 'success'`, so it is skipped. In
  `data.yml`, the failing step stops the job before publish.
- **I:** a persistent repair need would therefore stop *all* new data (deadlines included) in every
  run, silently except for alerts, while the published asset stays frozen at the last good export.
- **How an operator detects it (F, from code):**
  - the `db-update` step fails with exit 3, and its log or step summary prints the `quality:` issues
    naming the blocked repair and row counts;
  - the notify webhook gets "FPL agent data update needs attention (exit 3)";
  - in the DB, `source_attempts` rows show `exit_code = 3` with no new `fpl_players_gw.snapshot_at`
    and no new `fpl_deadlines.observed_at`;
  - the release asset's `updated_at` stops advancing.
- **Remediation addition:** §4 step 4 must also check that, after deploy, the first runs exit 0 (not
  3). If a repair is blocked, it needs a reviewed, backed-up `--repairs` run, authorised separately.
  Otherwise no deadlines accumulate.

## Addendum 2 (2026-10-28 ~21:30Z): branch-state correction (supersedes §3 and §4 step 1 where they conflict)
Earlier text said the export hardening was "uncommitted" and "not committed". **That is wrong.**
There are three separate code states:
| State | Export/publish behaviour (F, from `git show <rev>:<file>`) |
| --- | --- |
| **Production: master `51f0def`** | `export_parquet` skips empty tables; no manifest; `data_publish.sh` is a plain `tar` plus `--clobber` with no check and no dated copy; `data_restore.sh` has no manifest check. |
| **Committed local HEAD `c6a0c5f`** (branch `task/phase1-dashboard`, not on master) | `database.py` already has the manifest (`MANIFEST = "manifest.json"`), empty-table export, `verify_export`, and legacy-export handling ("missing or legacy export"). `data_publish.sh` already runs `verify-export` before upload and adds a dated `football-db-<stamp>.tar.zst`. `data_restore.sh` checks the manifest. |
| **Uncommitted follow-up** (working tree; Pablo's four-file patch: `database.py`, `cli.py`, `data_publish.sh`, `data_restore.sh`, +374/−32 against HEAD) | Adds `verify-export --complete` plus further publisher and restore safety, according to Pablo and god. I did not review its content. |

Consequence:
- **F:** neither HEAD nor the follow-up patch is on master, and the production workflows run master.
- **I:** the follow-up patch alone does **not** close the gap. Production gets manifest/complete
  exports only when HEAD's commits *and* the reviewed follow-up are merged to master. That merge
  also brings the provenance tables from §4 step 2.
- §4 step 1 should read: "merge the committed HEAD export hardening **plus** the reviewed follow-up
  patch to master, after Wegz review, god authorisation and Linux CI."
