# Operator / restore runbook — PREPARED, NOT EXECUTED (needs the user's approval)

Applies the database changes of this batch to `data_cache/football.duckdb`. Every step was rehearsed on
disposable copies (`chain.json`, `roundtrip-*.json`, `repair-rehearsal-*.json`); production was never opened
for write. Times are from the rehearsal on this machine (prod snapshot, 21.5 MB, 28 tables).

## What changes in production when the new code first opens it
- Opening adds five empty declared tables: `player_codes`, `migration_log` (R1), `repair_log` (R2),
  `fpl_deadlines` (U06), `source_attempts` (U05, after the Codex review). `FootballDB.created_tables`
  names them. Additive only.
- The R1 relabel (`fpl` -> `fpl-2026`, 1,068 rows) happens only when `migrate` is run explicitly.

## Steps
0. Read-only preflight (2026-10-24): `fpl-agent db-schema --db data_cache/football.duckdb` (exit 3 and the five
   tables above as `missing_tables`, `column_differences` empty; any column difference means STOP: no open alters
   an existing table, the new code refuses to open it). `fpl-agent source-health --db …` records the pre-apply state.
1. Stop the dashboard and any scheduled job that writes (DuckDB allows one writer process).
2. Snapshot (read-only source, refuses an existing destination; 0.4 s):
   `python artifacts/db-hardening-20261023T143050Z/snapshot.py data_cache/football.duckdb data_cache/backups/pre-apply-<UTC>.duckdb data_cache/backups/pre-apply-<UTC>.json`
   Check `"ok": true` and that `source_sha256_before` == `source_sha256_after`.
3. Export a second, independent backup (0.4 s): `fpl-agent db-update` is NOT used for this; use
   `python -c "from fpl_agent.db.database import FootballDB; FootballDB('data_cache/backups/pre-apply-<UTC>.duckdb').export_parquet('data_cache/backups/pre-apply-<UTC>-export')"`
   then `fpl-agent verify-export data_cache/backups/pre-apply-<UTC>-export` → exit 0.
4. Migrate (4.3 s): preflight must list no problems; `migrate` then a second `migrate` must report `noop-verified`.
5. Refresh without automatic deletes (9.4 s):
   `fpl-agent db-update --db data_cache/football.duckdb --season 2026 --points --no-repairs --football-data E0 --understat-leagues EPL`
   Exit 3 with `*_blocked` means a repair/quarantine is needed: read its `[rows: …]` impact, then decide
   (rerun with `--repairs`; every change is journalled and `fpl-agent repair-undo` lists/undoes it).
   Since the R1 review, no-repairs is the code default; `--no-repairs` is kept only for old scripts.
   The scheduled workflows (`data.yml`, `plan.yml`, `review.yml`) were not edited: after this code is
   deployed they stop with exit 3 instead of deleting when a repair is needed.
6. Export + verify (0.5 s) and, if publishing, the existing `scripts/data_publish.sh` (not exercised here: needs bash/zstd;
   the Linux archive path stays UNVERIFIED on this machine — run it only where CI's shell tests have passed).
7. Post-checks: `fpl-agent db-schema` → exit 0; `fpl-agent source-health` → read `blocked`/`degraded`;
   `fpl-agent db-eligibility --season 2026` → archive the manifest with the backup.

## Rollback
- Identity relabel: `fpl_identity.rollback(con)` (1.8 s in rehearsal; restores all 1,068 `fpl` labels).
- A journalled repair/quarantine: `fpl-agent repair-undo --db … <repair_id>` (exact; refuses after an intervening change;
  repeating a completed undo changes nothing and exits 0; it never adds tables to an older file).
- Whole file: restore the step-2 snapshot (copy back while nothing has the file open) or import the step-3 export into
  a NEW file with `import_parquet(require_manifest=True)` (1.1 s; 0 differences on every table in rehearsal). The
  import copies the export to a private folder and verifies/loads that copy (a file swapped afterwards cannot reach
  the database); an interrupted import leaves no rows and no "imported" marker, so it is simply rerun.

## Proposed recovery objectives (for the user/Codex to accept)
- RTO: ~5 minutes manual (file copy-back or import into a new file, verify, restart dashboard); measured restore 1.1 s.
- Data-loss window: back to the last export/snapshot. With the current schedule that is one refresh cycle; a snapshot
  before every write job (step 2, 0.4 s) would bound it to the job itself.


## Archive recovery clarification (2026-10-29 final review)

Restore fails closed if its chosen archive is damaged. If a newer dated archive is damaged
but the rolling asset is intact, explicitly select `DATA_ASSET=football-db.tar.zst` when
rerunning `scripts/data_restore.sh`. If the rolling asset is damaged, select a verified dated
asset with `DATA_ASSET=football-db-<YYYYMMDDTHHMMSSZ>.tar.zst`. Restore into a fresh directory
and verify the export before using it. Neither case permits skipping verification.
