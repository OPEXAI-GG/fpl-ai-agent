# Operator / restore runbook — PREPARED, NOT EXECUTED (needs the user's approval)

Applies the database changes of this batch to `data_cache/football.duckdb`. Every step was rehearsed on
disposable copies (`chain.json`, `roundtrip-*.json`, `repair-rehearsal-*.json`); production was never opened
for write. Times are from the rehearsal on this machine (prod snapshot, 21.5 MB, 28 tables).

## What changes in production when the new code first opens it
- Opening adds four empty declared tables: `player_codes`, `migration_log` (R1), `repair_log` (R2),
  `fpl_deadlines` (U06). `FootballDB.created_tables` names them. Additive only.
- The R1 relabel (`fpl` -> `fpl-2026`, 1,068 rows) happens only when `migrate` is run explicitly.

## Steps
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
   (rerun without `--no-repairs`; every change is journalled and `fpl-agent repair-undo` lists/undoes it).
6. Export + verify (0.5 s) and, if publishing, the existing `scripts/data_publish.sh` (not exercised here: needs bash/zstd).

## Rollback
- Identity relabel: `fpl_identity.rollback(con)` (1.8 s in rehearsal; restores all 1,068 `fpl` labels).
- A journalled repair/quarantine: `fpl-agent repair-undo --db … <repair_id>` (exact; refuses after an intervening change).
- Whole file: restore the step-2 snapshot (copy back while nothing has the file open) or import the step-3 export into
  a NEW file with `import_parquet(require_manifest=True)` (1.1 s; 0 differences on every table in rehearsal).

## Proposed recovery objectives (for the user/Codex to accept)
- RTO: ~5 minutes manual (file copy-back or import into a new file, verify, restart dashboard); measured restore 1.1 s.
- Data-loss window: back to the last export/snapshot. With the current schedule that is one refresh cycle; a snapshot
  before every write job (step 2, 0.4 s) would bound it to the job itself.
