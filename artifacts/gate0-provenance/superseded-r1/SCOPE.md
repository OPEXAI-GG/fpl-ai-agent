# Gate 0 provenance and schema slice

Author: Nabil (nabil-mvsc2c3l), 2026-10-29. This is a local artifact only. No commit, push, CI
dispatch, release, publish, migration, network ingestion, production action or identity apply was
made. It is **not production ready** (see §7).

## 1. What this is

`gate0-provenance.patch` (SHA-256 `b44a3457…89e6`) is layered on:
- production master **`51f0def`**, plus
- the approved export patch **`73751d56…f6a1`** (Pablo r3, approved by Wegz r3), unchanged.

**Applies cleanly (checked):**
- Starting from a fresh clone of `51f0def`, made with `core.autocrlf=false`, I ran
  `git apply --index` with the export patch and then this patch. Both apply.
- All 17 changed files are byte-identical to `tree/`.
- All 4 scripts keep index mode 100755. This patch touches no script.

**Hashes:** `hashes.sha256` covers the patch, `schema-inventory.json` and every file in `tree/`.

| File | Change | Why |
| --- | --- | --- |
| `db/schema.py` (+74) | 11 table declarations appended; the 23 master declarations untouched. **34 tables** (G4) | Verbatim from the reviewed working tree |
| `db/gameweeks.py` (+36) | `log_deadlines`, `official_deadline`, `_utc` | `fpl_deadlines` writer and reader. Identical to the working-tree file (`0cf7d2f7…`) |
| `db/ingest_fpl.py` (+2) | `load_bootstrap` calls `log_deadlines` in the same transaction as the snapshot | Records the deadline with every bootstrap. FPL identity changes (R1) **excluded** |
| `db/database.py` (+23) | `FootballDB.log_odds` (append-only, a row only when the price changed) | `match_odds_log` writer |
| `db/ingest_football_data.py`, `data/oddsapi.py` (+1 each) | Call `log_odds` after the `match_odds` upsert | Odds capture. Both files are identical to the working tree |
| `health.py` (+14 −3) | `SourceResult.started_at` / `finished_at`, naive UTC | Attempt timing. Identical to the working tree |
| `pipeline.py` (+33 −1) | `record_attempts` (its own transaction, URL query strings removed, a failure to record never changes the exit code); `_finish` calls it on **every** exit | `source_attempts` writer |
| `db/quality.py` (+200 −83) | R2 journal: `Journal`, `rollback_repair`, `RepairConflict`, `REFERENCES`. `repair`/`quarantine` rewritten to journal every row they remove or rewrite into `repair_log`, in the same transaction | G2 (§3). Verbatim working-tree code. `blocked_writes`/opt-in **excluded** |
| `tests/test_provenance.py` (new, 9 tests) | Schema and export of all 34; versioned deadlines; one update records deadlines, attempts and odds, and a rerun adds no duplicates; changed price → new observation; exit 2 recorded; exit 3 unexported, alerted, recorded; query string sanitised; quarantine and merge journalled and rolled back exactly | |
| `tests/test_pipeline.py`, `tests/test_backfill.py` (+3 −2 and +2 −1) | Idempotency tests skip `source_attempts`, which grows by one row per source every run by design | Same edit as the working tree |

**Excluded:**
- identity (R1 `fpl_identity`, `player_codes`/`player_links` writers, `db-links`);
- opt-in repairs (`blocked_writes`, `--repairs`);
- `repair-undo` CLI, `source-health` and `db-eligibility` CLIs;
- `cli.py` and scripts;
- model, dashboard and workflows.

## 2. Schema inventory (G4)

`schema-inventory.json`: 34 declared tables with columns, class and the writer in this release.
- Master has 23. The 11 added: `fpl_deadlines`, `source_attempts`, `match_odds_log` (A),
  `repair_log` (C), `migration_log` (C), `player_codes`, `player_links` (B), `runs`,
  `news_evidence`, `scout_cache`, `scout_calls` (D).
- With the approved exporter, every declared table is exported, empty ones included, and the
  manifest lists all 34 (tested).
- **Migration:** a 23-table database opened with this code gains the 11 tables through
  `CREATE TABLE IF NOT EXISTS` (master's `FootballDB.__init__`). No column of an existing table
  changes.
- The 23→34 migration on the pinned `r20261028` archive (G3) is **not rehearsed** here.

## 3. R1/R2 audit on master `51f0def` (G2)

- **R1 (identity relabel): no write path on master.** `fpl_identity.py`, `db-links` and the relabel
  are not on master or in this patch. `migration_log`, `player_codes` and `player_links` are
  declared and stay empty here. Identity is the separate G6 decision.
- **R2 (automatic repair and quarantine): active on master.**
  - `pipeline.run_update` and `run_backfill` call `repair(db)` before loading and `quarantine(db)`
    after.
  - On master these DELETE and UPDATE rows with no record (split-season matches, wrong club links,
    duplicate teams and matches, impossible shots and prices, self-matches, score conflicts).
  - G2 makes a journal a prerequisite, so this patch journals each removed or rewritten row
    (before and after image) in `repair_log`, in the step's own transaction.
  - `rollback_repair(db, repair_id)` undoes one step exactly, or raises `RepairConflict` and writes
    nothing.
  - **Behaviour kept:** repairs stay automatic, as on master. The working tree's opt-in mode is
    excluded on purpose: it makes a needed repair stop the whole update before loading (K3). That
    is a separate decision.
  - **Gap:** there is no CLI for rollback in this release (`repair-undo` is excluded with Pablo's
    `cli.py` scope). An undo needs a Python call on a restored copy.

## 4. Exit codes and no-load behaviour

Master semantics are unchanged; each outcome is now recorded in `source_attempts` (tested):

| Outcome | Exit | What happens |
| --- | --- | --- |
| Bootstrap fails | 2 | Nothing loaded, no deadline |
| A critical source fails later | 2 | — |
| Quality errors after loading | 3 | Data stays in the database; export skipped, so the published asset is not replaced; alert sent |
| All good | 0 | Data loaded; export runs |

- **No new "loads nothing" path:** repairs are not opt-in here, so the K3 path does not exist in
  this slice.
- **Not recorded (code reading):** an exception raised *outside* a guarded source (for example
  inside `repair()`) ends the run before `_finish`, so no attempt row is written. That case
  remains visible only as a failed job.

## 5. Prospective writers (no network used)

- **`fpl_deadlines`:**
  - Written by every `db-update` from the public `bootstrap-static` `events`. No FPL token is
    needed (K2).
  - A row is added only when a gameweek's deadline differs from the newest stored one, so
    `observed_at` versions moves.
  - Inside the bootstrap transaction: the deadline commits or rolls back with the snapshot.
- **`source_attempts`:** one row per attempted source per run, written after the data work in its
  own transaction. A failure to record is reported, never fatal.
- **`match_odds_log`: capture, and when it stays empty.**
  - (a) `football-data` CSV prices are logged on every `db-update` (E0/E1). They are published
    **after** matches, so their `ingested_at` is after kickoff and a cutoff reader correctly
    ignores them. They give provenance, not pre-deadline odds.
  - (b) Pre-deadline prices come only from The Odds API in the **plan** run (`agent.py:176-183`),
    and only when the `ODDS_API_KEY` secret is set. Otherwise the run notes "odds skipped".
  - Not verified here:
    - whether that secret exists;
    - whether the plan run's odds rows are in the published asset (the publish step's order
      relative to the agent step).
  - **Expected:** pre-cutoff odds stay empty (`pre_cutoff_odds_matches: 0`) unless that key is
    set. This is justified: current eligibility does not require odds.

## 6. Checks (exact commands, real results; Windows, existing `.venv`, scratch clone)

- `PYTHONPATH=src PYTHONUTF8=1 .venv/Scripts/python.exe -m pytest tests/test_provenance.py` →
  **9 passed**.
- Full suite:
  `pytest tests --deselect tests/test_workflows.py::test_helper_scripts_are_executable_and_strict`
  → **504 passed, 16 skipped, 14 deselected**, 0 failed.
  - Skipped: the shell tests that need Linux and zstd.
  - Deselected: the parametrized exec-bit test, which fails on Windows on unpatched master too
    (Pablo §4).
- Before the two idempotency-test edits, those two tests failed only on `source_attempts` row
  growth; the diff showed only that table.
- `ruff check` and `ruff format --check` on `src` and `tests` → clean.

## 7. Before any release candidate (not done here)

1. **Independent review:** Wegz reviews this exact patch hash. A leakage or safety review of the
   R2 change is recommended.
2. **Linux CI with zstd:** the export patch's 15 shell tests, plus this suite.
3. **G3:** rehearse 23→34 on the pinned `r20261028` copy (Zaza).
4. **G4:** after an authorised deploy, the first verified export must show:
   - a manifest listing all 34 tables;
   - `fpl_deadlines` holding the upcoming gameweek's deadline, observed before it;
   - `source_attempts` non-empty, with exit 0 runs.
   The next deadline must be confirmed from the official source at that time.
5. **G5:** GWs 1–8 can never become eligible. The first eligible gameweek is the first whose
   official deadline is observed by a deployed run before that deadline passes, with snapshot
   coverage. It cannot be named until the deploy date is known.
6. **Decisions for god:**
   - keep automatic journalled repairs (this patch) or move to opt-in (K3 risk);
   - whether a `repair-undo` CLI must ship before release.
