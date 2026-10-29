# Gate 0 provenance and schema slice (revision 2, FAA-41)

Author: Nabil (nabil-mvsc2c3l), 2026-10-29. This is a local artifact only. No commit, push, CI,
release, publish, migration, network ingestion, production action or identity apply was made.
It is **not production ready** (§8).

**Revision 2 changes** god's 07:44Z decisions on G2/K3:
- repairs are opt-in and off by default;
- a blocked repair stops the run visibly before loading;
- a tested `repair-undo` command;
- an exception in a repair, quarantine or check step is always recorded.

Revision 1 (patch `b44a3457…89e6`) and its scope, inventory and hashes are kept unchanged in
`superseded-r1/`.

## 1. What this is

`gate0-provenance.patch` is layered on:
- production master **`51f0def`**, plus
- the approved export patch **`73751d56…f6a1`** (Pablo r3, Wegz r3), unchanged.

**Applies cleanly (checked):**
- Starting from a fresh clone of `51f0def` (`core.autocrlf=false`), I ran `git apply --index`
  with the export patch, then this patch. Both apply.
- All 18 changed files are byte-identical to `tree/`.
- All 4 scripts keep 100755. No script or workflow changed.

**Hashes:** `hashes.sha256`.

| File (+/−) | Change |
| --- | --- |
| `db/schema.py` (+74) | 11 declarations appended; master's 23 untouched. **34 tables** |
| `db/gameweeks.py` (+36), `db/ingest_fpl.py` (+2) | `log_deadlines` / `official_deadline`. The deadline is written in the bootstrap snapshot's transaction |
| `db/database.py` (+23), `db/ingest_football_data.py` (+1), `data/oddsapi.py` (+1) | `log_odds` → `match_odds_log` (append-only) |
| `health.py` (+14 −3) | Attempt start and finish times (naive UTC) |
| `db/quality.py` (+253 −83) | R2 journal (`Journal`, `rollback_repair`, `RepairConflict`, `REFERENCES`); `repair`/`quarantine` journal every change in `repair_log`. **New in r2:** `blocked_writes` probes a step on an in-memory copy and turns any change it would make into an error. Both ported verbatim from the reviewed working tree |
| `pipeline.py` (+79 −8) | `UpdateOptions.repairs` / `BackfillOptions.repairs` = **False**; `_repair_step` and `_quality_gate` (r2); `record_attempts` on every exit |
| `cli.py` (+69) | **New in r2:** `--repairs` for `db-update` and `backfill` (opt-in); `repair-undo [--db] [id]` lists the journal, or undoes one repair exactly |
| `tests/test_repair_optin.py` (new, 10 tests) | §4 |
| `tests/test_provenance.py` (new, 9 tests) | 34-table export, deadlines, attempts, odds, journal rollback |
| `tests/test_pipeline.py` (+2 −1), `tests/test_backfill.py` (+4 −3) | Idempotency tests skip `source_attempts` (it grows by design). The unknown-competition test ignores the new `step:` rows |

**Excluded:** identity (R1, `db-links`, identity tables' writers), `source-health` and
`db-eligibility` CLIs, workflows, model and dashboard.

## 2. Schema inventory (G4)

`schema-inventory.json` (revision 2): 34 tables, with columns, class and writer.
- **Added tables** (11): `fpl_deadlines`, `source_attempts`, `match_odds_log` (A); `repair_log`,
  `migration_log` (C); `player_codes`, `player_links` (B); `runs`, `news_evidence`,
  `scout_cache`, `scout_calls` (D).
- **Export:** every declared table is exported, including empty ones.
- **Migration:** a 23-table file gains the 11 tables on open (`CREATE TABLE IF NOT EXISTS`). No
  existing column changes. G3 rehearsal on the pinned archive is **not done** here.

## 3. R1/R2 (G2) and the K3 behaviour

- **R1:** no write path on master or in this patch. `migration_log` and the identity tables stay
  empty; identity is the separate G6 decision.
- **R2: opt-in and off by default.**
  - Without `--repairs`, `repair` (before loading) and `quarantine` (after loading) run only on an
    in-memory copy (`blocked_writes`). If they would change any table, the run gets an error.
  - With `--repairs`, they apply, and every removed or rewritten row is journalled in
    `repair_log` in the same transaction. `fpl-agent repair-undo <id>` reverses it exactly, or
    refuses (exit 1, nothing written) when the data changed since or the id is unknown.
  - `repair-undo` refuses a file without all 34 declared tables, so an undo never migrates a file.
- **No workflow opts in:** checked by a test that reads `.github/workflows/*.yml`.

| Situation (default, no `--repairs`) | Result |
| --- | --- |
| Repair needed | **Exit 3 before any fetch or load**: nothing loaded (no snapshot, no deadline), no export, alert names `repair_blocked` with row counts, attempt recorded (`step:repair`, exit 3). Backfill behaves the same |
| Quarantine needed | Exit 3 after loading: rows kept and not deleted, **no export**, alert, attempt recorded |
| A repair, quarantine or check step raises | Recorded as a failed critical `step:*` attempt with the error, exit 3, no export, alert. A failed repair step loads nothing |
| Bootstrap or critical source fails | Exit 2, recorded |
| Clean | Exit 0, export |

**K3 trade-off, as decided:** while a repair is needed and not approved, every run is blocked,
deadlines included. It is visible through:
- the exit 3 step failure and its log line;
- the webhook alert;
- `source_attempts` rows with exit 3;
- the asset's `updated_at` stopping.

Recovery is a reviewed, backed-up `db-update --repairs` run, authorised separately.

**Cost (inference, not measured):** each default run copies the whole database into memory twice
(the repair probe and the quarantine probe). This was not measured against the production file
size; check it in G3.

## 4. Regression coverage (`tests/test_repair_optin.py`)

- Off by default for update and backfill; no workflow passes `--repairs`.
- Needed repair, update: exit 3, data unchanged, no deadlines, `repair_log` empty, no export,
  alert, single `step:repair` attempt with exit 3.
- Needed repair, backfill: blocked the same way and recorded as `backfill`.
- Needed quarantine: exit 3, bad row kept, no export, alert, attempt exit 3.
- `--repairs`: the merge is applied and journalled. A later undo that would collide with rows
  loaded afterwards is refused (exit 1) and writes nothing.
- CLI: lists the journal; undoes every step back to the exact pre-repair data; a repeat is a
  no-op. An unknown id → exit 1; an older-schema file → exit 1 and the file is unchanged.
- `repair`, `quarantine` and `run_checks` each made to raise: recorded with the error, exit 3, no
  export, alert; nothing loaded when the repair step raised.

## 5. Prospective writers (unchanged from r1)

- **`fpl_deadlines`:** versioned, from the public bootstrap, so no token is needed.
- **`source_attempts`:** every exit, in its own transaction, URL query strings removed. New in r2:
  repair, quarantine and check steps appear as `step:*` rows.
- **`match_odds_log`:**
  - football-data prices are post-match, so a cutoff reader correctly ignores them;
  - pre-deadline prices come only from the plan run's Odds API, and only with the `ODDS_API_KEY`
    secret (not verified);
  - pre-cutoff odds may stay empty, which is justified because eligibility does not need odds.

## 6. Residual visibility gap (code reading)

- An exception outside a guarded source or step (for example in `next_event` or in `_finish`
  itself) still ends the run without an attempt row. It shows only as a failed job.
- The repair, quarantine and check paths, which god's request covers, are guarded.

## 7. Checks (Windows, existing `.venv`, scratch clone; exact results)

- `PYTHONPATH=src PYTHONUTF8=1 .venv/Scripts/python.exe -m pytest tests/test_repair_optin.py
  tests/test_provenance.py` → **19 passed**.
- Full suite `pytest tests --deselect
  tests/test_workflows.py::test_helper_scripts_are_executable_and_strict` → **514 passed, 16
  skipped, 14 deselected**, 0 failed.
  - Skipped: the Linux/zstd shell tests.
  - Deselected: the Windows exec-bit test (14 cases), which fails on unpatched master too.
- `ruff check` and `ruff format --check` on `src` and `tests` → clean.

## 8. Before any release candidate (not done here)

1. **Review:** Wegz reviews this exact patch hash (R2, CLI and pipeline paths).
2. **Linux CI with zstd.**
3. **G3 (Zaza):** 23→34 rehearsal on the pinned `r20261028` copy. It should include the default
   run's repair and quarantine probe: is a repair currently needed on production data (that run
   would exit 3), and how much memory does the probe use?
4. **G4:** the first verified export after an authorised deploy must show:
   - 34 tables;
   - a deadline for the upcoming gameweek, observed before that deadline;
   - `source_attempts` with exit 0.
5. **G5:** GWs 1–8 can never become eligible. The first eligible gameweek depends on the deploy
   date.
