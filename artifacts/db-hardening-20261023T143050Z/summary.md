# DB hardening batch — evidence summary (Claude; awaiting Codex)

Evidence root: `artifacts/db-hardening-20261023T143050Z/`. Started 2026-10-23T14:30:50Z, HEAD b8f05e9.

## U00 baseline (done)
- Production `data_cache/football.duckdb` sha256 9bc925c1…305e1d (28 tables), read-only ATTACH only; hash identical before/after.
- `snapshot.py SOURCE DEST OUT` = in-memory coordinator, `ATTACH … (READ_ONLY)`, BEGIN/COPY FROM DATABASE/COMMIT,
  refuses an existing DEST (or .wal), compares table list, column shapes and per-table `count(*), sum(hash(row))`.
  - `prod-snapshot.duckdb` b453b7c9… ok (28 tables, 0.44 s) — `prod-snapshot.json`
  - `work-snapshot.duckdb` 92ce9f1a… from weekly `work.duckdb` (0ae2527a…, unchanged) ok (30 tables) — `work-snapshot.json`
- `dirty-paths.txt` (33 entries at start), `code-files.sha256` (src/tests/pyproject fingerprint).
- Baseline regressions: `pytest tests/test_db.py tests/test_fpl_identity.py tests/test_fpl_identity_evidence.py tests/test_quality.py tests/test_pipeline.py` → 122 passed, 60.6 s.

## U01 R1 integration
- Readers that accept both `fpl` and `fpl-<season>` match labels (dashboard overview/planning) JOINed match_ids, so a
  match under both labels came back twice (reproduced on a copy: 40 rows for 20 matches). Fixed with a semijoin
  (`src/fpl_agent/dashboard/service.py`); regression `test_a_match_under_both_fpl_labels_is_listed_once`.
  `ingest_fpl.load_live` (dict/set of fixture ids) and `review._cutoff` (min) were already insensitive to duplicates.
- Declared schema: `player_codes`, `migration_log` are in `schema.TABLES`; export/import/restore cover them (U03 roundtrip).
  Opening a pre-R1 file (production) with this code CREATES those two empty tables — an additive write. Now exposed as
  `FootballDB.created_tables`; behaviour not changed (changing it would alter the unattended update path).
- Process note: during one negative-control I ran `git stash` / `git stash pop` on the work tree; it restored cleanly
  (diff stat and dirty-path count re-checked, stash list empty). Not repeated; negative controls are done on copies.

## U03 export/import (first batch)
Expected-failure (pre-fix) results of new tests: reordered columns → ConversionException (positional insert);
undeclared column accepted; manifest/file column mismatch not detected; `[]`/non-dict manifest → AttributeError;
`../escape` and unknown table names accepted by verify_export. Fixes in `database.py`:
- manifest shape validated, only declared table names accepted, file columns must equal manifest columns and be declared;
- import by column NAME, refuses undeclared columns atomically; append-only tables use `EXCEPT ALL` (identical events kept);
- `missing_tables()` names declared tables an older export lacks (accepted as a merge source).
- `roundtrip.py`: export → restore → reimport → EXCEPT ALL both ways on every table.
  prod snapshot: 28→30 tables (constructor adds 2), export 0.41 s, restore 1.07 s, 0 differences, reimport 0.
  work snapshot: 30 tables, export 0.37 s, restore 1.10 s, 0 differences, reimport 0.
- tests: persistence+db+identity → 76 passed, 4 skipped (shell-script tests skipped on Windows: need bash+zstd).

## U02 reversible repair/quarantine (R2)
- `quality.REFERENCES`: explicit registry of every canonical match/team/player id column; `NOT_REFERENCES` names
  `migration_log.canonical_id` with a reason. `test_reference_registry_matches_the_declared_schema` fails if a new
  id column is added without being classified. MATCH_TABLES/TEAM_COLUMNS derive from it.
- Defect closed: `match_odds_log` was missing from every match delete/merge (quarantine/repair left orphan odds
  observations; a merge left them on the deleted duplicate). Now deleted (journalled) or moved with timestamps intact.
- New declared table `repair_log` (repair_id, seq, step, table_name, action removed|added, image, reason, logged_at,
  rolled_back_at). `Journal.delete/move/rewrite` write before/after images in the SAME transaction as the change.
- `rollback_repair(db, id)`: strict reverse order, all-or-nothing; refuses (RepairConflict, nothing written) when a
  written row changed since, a removed row would collide on its key, or the id is already rolled back.
- JSON columns are journalled as stored TEXT. Found by the real-data rehearsal: first rollback was NOT exact
  (fpl_players_gw.raw re-encoded, 2806→2567 chars); fixed and regression `test_rollback_keeps_json_text_exactly`.
- `blocked_writes` (dry probe) now reports per-table row impact from the probe's journal; repair_log excluded from
  change detection. Exception path still restores catalog (existing test).
- Entry points: `backfill --no-repairs` / `BackfillOptions.repairs` (same contract as `db-update --no-repairs`:
  probe only, BAD_DATA exit 3 and no load when a change is needed). New CLI `repair-undo [--db] [ID]` (list read-only
  / undo). **Default unchanged** (repairs on): they are now journalled and reversible rather than silent; making
  `--no-repairs` the default would change the user's unattended update and is left as a recommendation.
- Rehearsals on disposable copies (`repair_rehearsal.py`, `repair_rehearsal_corrupt.py`):
  - work & prod snapshots: dry probe = no change needed; applied nothing; dry wrote nothing.
  - INJECTED corruption (labelled expected): self-match + bad shot + bad price on real work data → dry report
    `[rows: appearances removed 40, …, player_match_stats removed 618, shots removed 24 …]`, dry wrote nothing;
    apply 0.07 s; rollback 0.54 s; exact (`repair-rehearsal-injected-2.json`). First attempt (`…-injected.json`)
    failed on JSON text — kept as evidence.
- Tests: quality/pipeline/backfill/persistence/db/cli/dashboard/identity → 212 passed, 4 skipped, 90.6 s; ruff clean.

## U05 ingestion atomicity / overlap (first batch)
- Defect closed: `export_parquet` copied tables one autocommit statement at a time, so a second connection to the
  same file committing mid-export produced a mixed snapshot (expected-failure reproduced: decisions 2 vs 1). Now one
  read transaction (ROLLBACK after). Regression `test_an_export_is_one_snapshot_while_another_connection_writes`.
- Defect closed: a failed final rename after moving the old export aside left no export at the published path.
  The previous export is renamed back. Regression `test_a_failed_swap_keeps_the_last_good_export_in_place`
  (not run against the old code; the old code path renames `out` away before the failing rename).
- Injected crash AFTER each source's full write (fixtures+bootstrap, live points, football-data CSV, Understat league):
  rows, `fpl_points` success marker and ingest_log all roll back (`test_a_crash_midway_through_a_source_leaves_no_partial_rows`,
  4 cases). The football-data/Understat cases are weak (a rerun upserts identical rows); fixtures and points are strong.
- Tried and REVERTED: recording every source outcome (incl. failures) in `ingest_state('health', …)`. It contradicts
  the existing contract `test_failed_bootstrap_leaves_database_untouched`. Current behaviour is already honest per
  source (dashboard `last_success_at` comes only from successful loads, so a failed source ages; failures are in the
  db-update output/alert). Open design question for Codex: a separate run-outcome ledger vs the untouched-on-failure rule.
- Cross-process: DuckDB allows one read-write process per file; the dashboard opens read-only and reports
  `db_locked` (existing test). Multiple writer processes are unsupported (second open fails); not changed.

## U04 security (DB03) — see `threat-model.md`
- Fixed: S1 manifest shape/table-name validation, S2 positional import (U03 changes).
- Open: S3 certifi 2026.7.22 PYSEC-2026-4320 (fix 2026.10.19; `pip-audit.txt`, needs approval to upgrade);
  S4 restore-script symlink/legacy-archive gaps (shell path not runnable on Windows); S5/S6 multi-user blockers.
- Dynamic SQL: every interpolated identifier in src is a code constant, `?` placeholder list or declared table;
  dashboard search/sort/paging are in memory. No credentials or token files were read.

## U06 integrity/provenance (first batch)
- Defect closed (cutoff leakage, F-17 trace): the season position fill used every snapshot of the season, and
  replay/backtest build one frame for all gameweeks, so a later snapshot could add/remove an earlier cutoff's fill.
  New `features.season_positions(db, as_of)` / `positions_as_of(frame, db, cutoff)`; `project()` applies it whenever a
  cutoff is given. Live plan unaffected (its snapshot predates its cutoff). Regression
  `test_season_position_fill_is_cutoff_invariant` (later disagreeing snapshot leaves the earlier cutoff unchanged;
  before the first snapshot there is no fill). Price/status/team/chance stay NULL without own-GW snapshot (unchanged).
- New prospective observation contract: `fpl_deadlines` (append-on-change versions, `observed_at`), written inside
  `load_bootstrap`'s transaction; `gameweeks.official_deadline(db, season, gw, as_of)`; `review._cutoff` prefers it,
  else the old first-kickoff−90 min approximation. Replay/backtest NOT switched (no past deadlines stored; would change
  evaluation semantics). Regression `test_official_deadlines_are_versioned_observations`.
- F-16 BLOCKED: needs an independent match report and raw upstream payloads that were never retained; disagreements
  stay labelled (no correction manufactured).
- Per-source failure health state: attempted and reverted (U05 note).
- Tests: point_in_time/review/db/quality/persistence 90 passed, 4 skipped; minutes 21 passed; project/replay/backtest 31 passed.

## U07 performance — `perf-baseline.json`, `perf-growth.json`, `perf-minutes-experiment.json`
- First baseline was distorted by tracemalloc during timing (kept as `perf-baseline-tracemalloc-distorted.json`).
- Warm medians (work snapshot): player_gw_frame 0.09 s, minutes_features 0.65 s, match_frame 0.03 s,
  run_checks 0.08 s, dry repair+quarantine probes 0.51 s, export 0.36 s. 10x synthetic growth: 0.42 / 0.96 s.
- Vectorised rolling features: identical output but < 2x on the full function (pre-set gate) → not adopted.
- Real failure seen: rapid repeated export hit Windows "Access is denied" on rename (OneDrive-synced folder);
  the last good export stayed in place and verified. Added a bounded rename retry + test.

## U08 — partial
- chain.py rehearsal ok (`chain.json`), dry plan smoke ok (`plan-smoke.log`), lint ok (`lint.log`), CLI smoke ok.
- Full suite with --cov-fail-under=85: STOPPED by the harness at ~78% (system low on memory), no failures before
  that (`full-suite.log`). Not restarted. Coverage gate unverified.
