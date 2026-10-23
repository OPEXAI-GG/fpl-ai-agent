# DB01–DB02 audit: `data_cache/football.duckdb` (revision 2, 2026-10-22)

Status: **resubmitted after Codex review; not self-approved. No production mutation.**
Revision 2 responds to the four required follow-ups in `TASK.md` ("Codex independent review of
DB01-DB02"). Changes from revision 1 are listed in section 9. DB03–DB08 are not started.

## 1. Method and safety

- Every query opened the database with `duckdb.connect(path, read_only=True)`. `FootballDB.__init__`
  (writable, runs `CREATE TABLE IF NOT EXISTS`, `database.py:150`) was never called on the file.
  Existing code was run through a shim (`FootballDB.__new__`, `con` set to the read-only connection).
- No WAL file and no writer. The file sha256 was identical before and after, including after this
  revision: `9bc925c102c1bc108948de1ba9acd153d07f09550a02a7b7ec8780fa66305e1d` (31,993,856 bytes,
  mtime 2026-10-20T20:22:17Z). Nothing was copied, repaired, quarantined or refreshed.
- Reproducers ran only on `FootballDB(":memory:")`. The local audit (sections 1-8) used no network access, no credentials and no config change. The later, separately approved read-only inspection of the public `data` release is recorded in `release_inspection.md`, not in this report's evidence.
- Artifacts hold metadata, aggregates and ids. Player and club names of public football data appear in
  a few sample rows; no private payload (decisions, reviews, lessons, news text) and no secret does.
  The manager entry id exists in the DB and in `data_cache/dashboard/preferences.json`; it is not copied.

Environment: commit `b8f05e9`, branch `task/phase1-dashboard`; Python 3.13.12; DuckDB 1.5.6
(extensions core_functions, icu, json, parquet); Windows 11. Revision-1 audit time 2026-10-22T10:27Z,
about 38 h after the last write.

Run order (repo root, `.venv/Scripts/python.exe artifacts/db-audit-20261022/<script>`):
`audit_inventory.py` → `audit_checks.py` → `audit_ledger.py` (fails if a column has no outcome), then
`audit_archives.py`, `audit_semantic.py`, `audit_consumer_probes.py`, `audit_storage_paths.py`,
`audit_fallback_coverage.py`, `repro_id_reuse.py`, `repro_repair_orphans.py`.

| Artifact | Content |
|---|---|
| `inventory.json` | actual tables/columns/constraints, drift, files, archive and backup hashes |
| `coverage.csv` | 237 rows, one per column: meaning/unit, grain, keys, source, textual writers/readers, update/time semantics, retention, sensitivity, null/distinct/min/max, **`check_ids`, `ambiguous_check_ids`, `audit_outcome`** |
| `checks.json` | 341 checks: SQL, violations, denominator, examples, `columns` referenced, `classification` |
| `ledger_summary.json` | outcome counts and the (empty) list of untested columns |
| `measurements.json` | coverage, freshness, link and stat coverage, news state, projection runs |
| `semantic_sampling.json` | stratified traces over retained evidence, failed-check dispositions, ownership semantics |
| `consumer_probes.json` | real consumer functions run read-only: row grain and source selection |
| `storage_paths.json` | configured stores (defaults) against what exists; presence-only effective environment |
| `fallback_coverage.json` | what FPL players without a FotMob id actually receive |
| `archives.json` | the calibration archive and whether it is point-in-time |

## 2. DB01: storage surface

- 28 tables, 237 columns, 0 views, 0 sequences, 0 secondary indexes. **0 foreign keys, 0 CHECK, 0 UNIQUE
  constraints**; 20 tables declare a primary key, 8 do not (`projections`, `decisions`, `reviews`,
  `lessons`, `transfers`, `ingest_log`, `match_odds_log`, `scout_calls`).
- Schema drift against `schema.TABLES`: none. No schema-version table; `CREATE TABLE IF NOT EXISTS` never
  migrates an existing table (`database.py:155`).
- **Coverage contract (237 columns, `coverage.csv`)**: each has a resolvable outcome.

  | Outcome | Columns |
  |---|---|
  | checked: pass (a passing *predicate* check validates at least one non-uniqueness dimension) | 149 |
  | finding or dispositioned failure | 17 |
  | measured only (no pass/fail rule) | 1 |
  | referenced only: `ingest_log.source`, `projections.model` appear only in grouping/uniqueness or denominator SQL; **no value contract validated** | 2 |
  | not applicable, with reason (never written, or free text); not a security approval | 6 |
  | **not testable: table is empty (0 rows), so any check is vacuous** (fixture contracts belong to later tests) | 62 |
  | untested gap (no check references the column at all) | 0 |

  **"0 untested gaps" means every column is referenced by some check, not that its domain or meaning is
  validated.** Reference is not validation. A column counts toward a dimension only when it appears in a
  check's *violation predicate* (not the denominator, and not a `key` check, which only groups). Of the 169
  columns that are testable and applicable (nonempty table), the dimensions **not assessed** are: value contract
  57 columns (the other 112 of 169 have a predicate labelled domain/numeric/enum/JSON/contract; these are
  check categories, not proof of semantic correctness; 62 columns in empty tables and 6 not-applicable ones
  are outside the 169), presence 105, relation 129, temporal 140.
  The relation and temporal counts are high because most columns carry no foreign key and no time meaning;
  they are not claims of defects. `coverage.csv` records per column `predicate_check_ids`,
  `validated_dimensions`, `not_assessed_dimensions` and `not_assessed_owner_stage` (Claude, DB03 presence
  and value contracts, DB04 relations, DB05/R3 temporal, DB06/DB07 cross-source). The 57 columns without a
  value contract are the DB03 contract backlog; they are not written out here.
- **Gate (`audit_ledger.py`)**: exit 0 = audit complete (the six data findings are results, not errors);
  1 = incomplete or broken (untested gap, unclassified or errored check, duplicate check id, check
  referencing no column, schema drift or failed reconciliation in `inventory.json`); 2 = missing or
  unreadable input. Tested by `test_ledger_gate.py` (10 tests on isolated copies, see section 9).
- **Enum and payload contracts added in revision 2**: 21 enum drift detectors (allowed sets are the code
  constants where one exists, otherwise the values observed at audit time, so they detect drift and are
  not a specification); JSON contracts for `fpl_players_gw.raw`, `fpl_points_gw.stats/explain`,
  `projections.detail` (12 components present and equal to `xpts` within 0.05 in 26,720/26,720 rows),
  `decisions.payload` (11-player XI, 4 bench, ids exist), `runs.stages/reasons`, `news_evidence.payload`.
  All pass. Mechanical sweeps: every TIMESTAMP/DATE column not in the future, every `season` and `gw`
  in range.
- **Storage paths and configured stores (`storage_paths.json`)**:
  - `data_cache/snapshots` (the `SnapshotStore` default, `config.py:34`) **does not exist**, so the JSON
    point-in-time store in `data/store.py` holds nothing here. `SNAPSHOT_DIR` is not set in this shell.
  - Present: `dashboard/jobs.json`, `plans/`, `preferences.json` (holds the entry id), two server logs,
    three pre-change DuckDB copies and `calibration-study-20261020.duckdb` (all four hashed, none has a manifest).
  - Remote and **not inspected**: the GitHub release asset `data` (parquet export and manifest, written by
    `.github/workflows/data.yml` and `backfill.yml`, which backfill history from 2017/18). It may
    hold history this audit did not see.
  - Effective configuration, presence only, in this shell: `SNAPSHOT_DIR`, `AGENT_MODE`, `AGENT_ENABLED`,
    `AGENT_MAX_SNAPSHOT_HOURS`, `FPL_ENTRY_ID` and `ODDS_API_KEY` are all unset. These are defaults, not the
    dashboard server's or a scheduler's environment, which were not inspected.
- **Readers, writers and critical joins** (code-traced; the `coverage.csv` writer/reader columns remain a
  textual starting inventory):
  - 24 `upsert`/`append` call sites found by grep, all with a literal table name. At the sites read
    (`ingest_fpl`, `ingest_fotmob`, `ingest_football_data`, `oddsapi`, `runs`, `project`, `translation`,
    `scout`) the column set is a code-literal dict, so identifier interpolation (`database.py:173,208`) is
    reachable only from code-defined names there. Not read: `ingest_understat`, `ingest_vaastav`, `lessons`,
    `review`, `ownership`. Reachability from external data stays hypothesis H-5 (DB03).
  - Snapshot consumers: `model/project.py:66-79` (newest ≤ `as_of`, QUALIFY per `fpl_id`, then
    `drop_duplicates("player_id")`); `dashboard/service.py:332` (QUALIFY newest per `fpl_id`);
    `replay.pool_as_of` (row_number per player, latest gw/snapshot); `review._positions` (row_number per
    player); `cli.py:284` (row_number per player); `review._field_core` (joins on `(season, fpl_id)`, 2
    snapshots, removed by DISTINCT). All pick one snapshot per player.
  - **Bounded probes** (`consumer_probes.json`): `player_gw_frame` (4,551 rows), `match_frame` (6,862),
    `current_pool` (668), `pool_as_of` (668), `_positions` (668) each have 0 duplicate grain rows.
  - **New finding F-17** from the `player_gw_frame` probe: the snapshot join is `ON (season, gw, fpl_id)`
    and snapshots exist for GW8 only, so **all 4,551 current-season rows have NULL position, price, status
    and team**. `pool_as_of(2026, 5)` returns 0 rows.
  - Unresolved paths: dashboard and solver readers beyond those above; `cli.py:632`; `lessons.py:319`
    (`any_value(fpl_id)` per player, safe only while one fpl id maps to one player in a season).

## 3. DB02: check summary

**341 checks: 304 pass, 6 fail, 31 informational, 0 query errors.** (Revision 2 as first submitted said 342/305: one check id, `domain.matches.season_range`, was defined twice, a weaker 2000-2100 bound and the 2016-2100 sweep; the gate found it and the weaker one was removed.) The existing `quality.run_checks`
(including points-vs-rules rescoring) returns 0 issues; it covers 11 of the 31 relationship checks run
here, only the home-side score, no `match_odds_log` and no time semantics.

Clean on the full population (denominators in `checks.json`): 31 relationship checks; no NaN/Infinity;
JSON valid and `raw` agrees with extracted columns (1,336/1,336); FPL snapshot totals and minutes equal the
sum of per-GW rows for all 668 players; `explain` sums to `total_points` for 4,551/4,551; every FPL fixture
id maps to a match; 38 fixtures (19 home) per club, one per matchday; 11 starters per team in 324/324;
goals agree across football-data, understat and FotMob for 420/420 comparisons; shot "Goal" counts equal
the score in 162/162 matches.

### Disposition of the six failed checks (none promoted wholesale to a production failure)

| Check | Count | Disposition |
|---|---|---|
| `null.matches.matchday` | 396/776 | **valid by design**: cup matches have no round (`ingest_fotmob.py` sets `matchday` only for league rounds). Consumers must key cups by date. |
| `null.runs.gw` | 1/5 | **valid**: failed bootstrap run, no gameweek known. |
| `null.appearances.position` | 1/6,862 | **benign**: an unused UEL substitute (0 minutes, no stat rows). |
| `null.shots.xg` | 15/4,603 | **proxy-explained, contract needed**: all 15 are `Goal` shots and each lies in a match whose team goals exceed the sum of player goals (15/15). That is an **own-goal proxy**, not independent confirmation of attribution. NULL means "no xG supplied"; xG sums silently omit them. |
| `domain.matches.stale_unfinished_past_kickoff` | 16/776 | **stale state, not corruption**: 16 UCL matches kicked off 2026-10-20 19:00Z, last fetch 20:09Z was before full time. |
| `recon.fpl_vs_fotmob.goals` | 1/1,324 | **UNRESOLVED**: see F-16. |

## 4. Semantic sampling (`semantic_sampling.json`)

Retained raw evidence is limited to FPL (`fpl_players_gw.raw`, `fpl_points_gw.stats/explain`) and news
metadata. **FotMob, Understat and football-data raw payloads are not retained**, so FotMob-derived rows can
only be traced through their normalised rows. That is a gap, and nothing below is external truth.
The sample is seeded (20261022) and stratified.

- **Linked players**: of 345 linked FPL players with FotMob Premier League appearances, 345 are at the same
  club and 343 agree within 5 minutes in every shared match (the other 2 are the 7-minute cases below).
  10 linked midfielders are listed with their per-player comparison.
- **Refused links**: 92 position-disagreement pairs have Premier League appearances; **all 92** are at the
  same club with every shared match within 5 minutes. A strict position rule therefore refuses pairs that
  retained evidence corroborates (examples: a player FPL lists as MID and FotMob as FWD). 10 refused
  midfielders are listed. Unmatched players (including single-word names): of 137 FotMob Premier League
  players with no FPL id and at least 2 appearances, 126 have exactly one FPL candidate at the same club
  whose per-match minutes agree, 11 have several, none has zero. This is corroboration, **not applied** and
  not external truth; a short minutes history can coincide.
- **Cup matches**: 6 finished matches (2 each UCL, UEL, UECL) traced from `matches` through the FotMob
  `match_ids`, `team_match`, appearances (11 starters per side) and shots; clubs map to two distinct
  canonical ids and shot goals reconcile with the score (NULL-xG Goal shots are consistent with, but not proof of, own goals).
- **Goal discrepancy (match 40, 4-1)**: both sources total 4 goals for the home side, but FPL credits three
  players with one goal each and FotMob credits one player twice and another once; two FPL scorers have no
  FotMob goals and one FotMob scorer has no FPL id. Retained evidence cannot say which attribution is right.
  **Unresolved**; an independent match report is needed (F-16).
- **Minutes**: of 1,876 comparable player-matches FotMob equals FPL in 1,531, is 1 minute higher in 330, 1
  minute lower in 13 and **7 minutes lower in 2**. Revision 1 called the differences stoppage-time
  definitions; that explains the +1 cases only. The 15 negative cases are unexplained.
- **Team vs player goals**: 15/15 team-matches where player goals fall short coincide with a NULL-xG `Goal`
  shot in the match (own-goal proxy only). **Shot xG vs team xG**: 31/162 matches differ by more than 0.05, 26 of them
  without any NULL-xG shot; magnitude is small but unexplained (F-16). Understat vs FotMob team xG differs by
  more than 0.5 in 14/140 team-matches: two models, expected.
- **Ownership semantics** (`data/ownership.py`): `eo` counts pick multipliers (bench 0, captain 2), so
  `eo < owned` (114/156 rows) is expected for benched players; no per-pick data is retained to verify the
  bench explanation. The row labelled GW g holds the picks of **GW g-1** (`:85-100`, last deadline's picks,
  a fixed-seed sample of 200 from the top 10,000), so the label is a planning label, not the pick date.
  The 512 players with no row are outside the sample (unknown), not 0% and not population ownership.

## 5. Findings register

Class: **C** confirmed defect (reproduced), **R** design risk, **G** coverage gap (measured), **H**
unresolved/hypothesis. Each finding has an owner (Claude implements, Codex reviews) and the stage that
addresses it. "No observed corruption" is qualified: it excludes only the scenario tested.

| ID | Sev | Class | Finding and evidence | Code | Owner / stage |
|---|---|---|---|---|---|
| F-01 | High (latent) | C | FPL element, team and fixture ids are season-specific but the id tables key on `(source, source_id)` and lookups ignore season. `repro_id_reuse.py`: a second season reuses the 2026 player, club and match, and `_update_match` rewrites the 2026 score 2-0 to 0-1; one match row remains, expected two. The vaastav path is season-labelled (`vaastav-<season>`). No row is corrupted today because one FPL season is loaded; that excludes this scenario only. | `schema.py:10-12,15-17,23-25`; `database.py:231-251,290-310,330-336,373-378`; `ingest_fpl.py:36-82` | Claude / R1 (DB07) |
| F-02 | High (latent) | C | Repair and quarantine list a match's dependents by hand and omit `match_odds_log`, and delete with no retained copy. `repro_repair_orphans.py`: 1 orphan after quarantine, 1 after merge. 0 on the live file because the log is currently empty (a writer exists, `database.py:185`, and has not populated it here). | `quality.py:197-198,261,309-343,348-398,423`; `pipeline.py:103,178,348,401` | Claude / R2 (DB04, DB07) |
| F-03 | High for historical evaluation | G | Historical GW1-7 evidence for snapshot features is absent from the production file: `fpl_players_gw` has 2 snapshots, both GW8, which are timestamped and usable for GW8 decisions; none exists for GW1–7 (`time.snapshots_per_gw` 7/7 missing); no table stores the official deadline. Result-derived features (appearances, stats, shots) can be cut by kickoff but remain **event-time reconstruction without known-at or correction provenance** (bulk-loaded 2026-10-20, mean 585.7 h after kickoff). The 2024/2025 archive is separate and is a reconstruction (section 7). The local audit did not inspect the remote `data` release. A later read-only listing (`release_inspection.md`) observed one export version; historical row/season coverage and known-at provenance are not yet inspected, so no claim about the absence of dated history is made. | `schema.py:52-57`; `database.py:381` | Claude / R3 (DB07); label now |
| F-04 | Medium | G | No prospective odds in the file: 310/310 unfinished ENG1 fixtures have none; `match_odds_log` has 0 rows; all 280 stored prices were loaded after kickoff. The prospective writer (`oddsapi.py:102`) needs `ODDS_API_KEY`, which is unset in this shell (the scheduler's environment was not inspected). For these fixtures `market_lambdas` returns no market signal (effect on projections: H-1). | `project.py:127-157`; `oddsapi.py:27,102` | Claude / R4; owner decision on a key |
| F-05 | Medium | G | FotMob linkage covers part of the FPL population: 156/449 FPL players with minutes (34.7%; 40,876 of 137,657 FPL minutes, 29.7%) have no FotMob id; MID 109/214 linked. **Those players still have FPL's own underlying stats**: `expected_goals` and `defensive_contribution` are present on 714/714 played rows of unlinked players (`fallback_coverage.json`). However in the latest run's GW8 projection **all 310 unlinked players use `rate_source = prior`**, including 60 expected to play ≥45 minutes (36 MID, 17 DEF, 5 FWD, 2 GKP); linked players use `pl` (277), `translated` (27) or `prior` (54). Section 4 shows the position rule refuses pairs that retained evidence corroborates. Revision 1's "no wrong merges" claim is withdrawn: a name-similarity test cannot establish it, and the sampling above checks only the 345 linked players with Premier League appearances. | `database.py:253-288` | Claude / R7; model use is phase two |
| F-06 | Medium | R | Unknown vs zero is not enforced: `shots.xg` NULL in 15 Goal shots, an own-goal proxy not independently confirmed (omitted from xG sums); 125 absent player-gameweek rows (4,551 of 4,676) are "no row", not zero; 1,814 appearance rows have no stats rows, all with 0 minutes (`ingest_fotmob.py` `load_match` writes stats only if minutes > 0 or stats exist); `team_match.shots` NULL for all 140 understat rows. | `schema.py`; `features.py:101-127` | Claude / R6 (DB07) |
| F-07 | Medium | R | No relational or domain enforcement in the database; all 31 relationship checks and every range/enum rule pass today (the one domain failure is staleness) only because writers behave. | `schema.py` | Claude / R8 (DB07) |
| F-08 | Medium | R | Last-writer-wins on `matches`: `_update_match` rewrites kickoff, score, finished from whichever source loads last; `source` is the creator only; `ingested_at` is not refreshed. Sources agree today (0 of 420 goal comparisons differ). | `database.py:336,350,373-378` | Claude / R6 (DB04) |
| F-09 | Medium | R | Short keys are not row-unique: `(season, gw, fpl_id)` 2 rows for 668/668 keys; `(match_id, team_id)` up to 3 source rows for 140/324; `match_odds` 4 rows per match. The consumers traced in section 2 select correctly (probes: 0 duplicate grain rows); dashboard/solver readers beyond those are untraced. | `database.py:381` | Claude / R6 (DB04) |
| F-10 | Medium | R | Private decision data has no manager key: `decisions`, `reviews`, `lessons` carry no `entry_id`; a decision links to its run only by `created_at = decision_at`. Blocks multi-user until C06. | `schema.py:69,71,105,110` | Claude / C06, DB03 |
| F-11 | Medium | G | **Stale for GW8 at audit time**: last snapshot 2026-10-20 18:08Z against first GW8 kickoff 2026-10-23 19:00Z; 16 UCL matches recorded unfinished. Revision 1's "freshness is not machine-visible" was **wrong**: `dashboard/service.py:251-252` and `:543-545` expose `snapshot_age_hours` and `snapshot_stale` (threshold 6 h, `service.py:27`), and `execution/safety.py:103` blocks a live run on a stale snapshot (`config.py:35`, 6 h, env-overridable). What remains missing is **per-source** freshness (fixtures, results, news, odds, ownership have no age signal) and any machine state that distinguishes healthy/degraded/blocked per source. | `dashboard/service.py:27,251,543`; `safety.py:103` | Claude / R5 (DB07) |
| F-12 | Low | G | News evidence: 89 observations, 80 fresh (all "sports news"), 7 failed official-club fetches (`ConnectError`), 1 failed and 1 undated sports item. 0 official-club observations succeeded in the window. | `news.py` | Claude / DB07 |
| F-13 | Low | R | `projections` grows 5,344 rows/run with no pruning; run `28f1c3fc454e` has no `runs` row. | `schema.py:63-65`; `model/project.py` | Claude / DB07 |
| F-14 | Low | G | Minutes disagree: FotMob 1 higher in 330/1,876, 1 lower in 13, 7 lower in 2. Cause unexplained for the negative cases; the minutes source per consumer must be documented. | – | Claude / DB07 |
| F-15 | Info | G | `features.py:101` reads 8 of 28 FotMob stats; `shots` (4,603 rows) has no reader beyond ingestion. Textual evidence only. Candidates for phase-two ablation, not for use now. | `features.py:101-127` | Codex gate / phase two |
| F-16 | Medium | H | **Unexplained cross-source conflicts**: (a) one goal attributed to different players by FPL and FotMob in match 40; (b) shot xG sums differ from FotMob team xG by more than 0.05 in 26/162 matches without NULL xG; (c) two 7-minute minutes differences. Retained evidence cannot adjudicate; matching club and minutes does not establish identity, and differing xG totals alone do not establish a model or source error. **Evidence required**: an independent match report (scorers, minutes) and the upstream raw payloads, which are not retained (a recorded provenance gap). | – | Claude (needs independent source) / DB02 follow-up |
| F-17 | High for current-season features | C | `features.player_gw_frame` returns **NULL position, price, status and team for all 4,551 current-season rows** because snapshots exist for GW8 only and the join is per `(season, gw)`. Consumers of those columns see NaN on this file. Whether and how the minutes model copes (it may be trained from the archive) is untested (H-7). | `features.py:41-50` | Claude / DB04 probe, DB07 |

Hypotheses not yet tested, each with its stage: H-1 market-signal absence degrades projections (phase two);
H-2 `load_live` delete-then-upsert atomicity (`ingest_fpl.py:219-223`, DB04); H-3 fixtures with no
`kickoff_time` are skipped (`ingest_fpl.py:77`, DB04, needs a raw fixture); H-4 `_next_id` races
(`database.py:227`, DB04); H-5 identifier reachability from external data (DB03); H-7 minutes-model behaviour
on NULL snapshot columns (DB04/phase two). Revision 1's H-6 (ownership) is resolved in section 4.

## 6. Time semantics (DB02.5)

- Event time exists for matches and FPL points. Known-at exists only for `fpl_players_gw.snapshot_at`
  (2 snapshots), `projections.created_at`, `field_ownership.fetched_at` (overwritten per key), `runs`,
  `news_evidence.observed_at`. Ingestion time is bulk-load time; `matches.ingested_at` is first-insert time.
  The official deadline is stored nowhere (`dashboard/service.py` states this itself).
- Verdict: **snapshot features (price, availability, ownership, news) are replayable only for the two
  GW8 snapshots. Result-derived features are event-time reconstruction (cut by kickoff) without
  known-at or correction provenance.** Reconstructed history is ineligible for strict point-in-time
  evaluation (declared in `docs/football-intelligence.md`); it remains usable for explicitly labelled
  reconstruction experiments. Unknown known-at values stay unknown; kickoff and today's ingestion time are
  not substituted. A current snapshot is not history.

## 7. The historical archive (`archives.json`)

`data_cache/calibration-study-20261020.duckdb` holds 2024 (26,919 rows) and 2025 (29,338 rows) from the
vaastav archive: `chance_next` and `news` NULL in 100% of rows, `raw` holds only `was_home`, and 45,593/56,257
rows (81%) carry a `snapshot_at` after the first kickoff of their gameweek. It is an event-time
reconstruction. Holdout claims must cite this archive and these limits; they do not establish
point-in-time policy performance.

## 8. Remediation proposal

The R1–R3 specifications (with Codex's binding constraints) are in `TASK.md` under "Revised R1-R3
specification". R4–R8 are as in revision 1 with owners and stages in the register above. The full R1–R8
list is **not declared sufficient**: DB03–DB06 evidence is still to come. No model optimisation or tuning
while these gates are open.

## 9. Changes from revision 1 and verification

Changed: coverage contract finished (341 checks, per-column outcomes, JSON/enum contracts, storage paths,
traced readers/writers/joins, probes); semantic sampling added; six failures dispositioned; **withdrawn or
corrected**: "no wrong merges" (F-05), "no xG for unlinked players" (FPL xG exists; the projector uses priors),
"freshness is not machine-visible" (F-11), "none genuinely pre-deadline anywhere" (F-03), "never written" versus
"currently empty" (section 2), the one-directional minutes explanation (F-14); new findings F-16, F-17.

| Command (repo root) | Result |
|---|---|
| the scripts in section 1, in order | exit 0 each; 341 checks 304/6/31/0; ledger 237 columns, 0 untested gaps, audit complete |
| `ruff check` / `ruff format --check` on the audit folder | pass |
| `sha256sum data_cache/football.duckdb` | unchanged (`9bc925c1...305e1d`) |
| `pytest tests/test_db.py tests/test_quality.py tests/test_persistence.py tests/test_point_in_time.py -q -p no:cacheprovider --basetemp=<scratchpad>` (revision 1) | 58 passed, 4 skipped (shell tests need bash, tar, zstd; Linux gate open). Not rerun in revision 2: no code changed. |

Limits: internal consistency is not external truth; the local audit used no network and no remote release (see
`release_inspection.md` for the later listing-level inspection); no raw FotMob/Understat evidence; writer/reader columns are a starting inventory; the dashboard and scheduler environments were not
inspected; security, backup/restore, concurrency and performance are untouched.
