# Database strengthening: where the value is (2026-10-26)

Work: conv-a12bcb. Researcher: Pablo. Research only; nothing was changed in code or in any database.

## Answer

The highest-value improvement is **linking FPL players to their FotMob match data by appearance
evidence**. In the available database, 156 of 449 FPL players who have played this season (30% of all
FPL minutes, including Bukayo Saka, Marcus Rashford, John McGinn, Jack Grealish) have no FotMob
record joined to them. The model's per-player rates (xG, xA, defensive actions) are keyed on the FPL
player, so those players fall back to position priors. A strict appearance-matching rule recovers
127 of them and lifts linked minutes from **70.3% to 95.4%**, measured read-only below.

Ingestion speed is not a gap. Freshness and decision-time evidence are gaps in the local file, but
whether they are gaps in production is unknown (see Unknowns).

## What was measured, and on what

- Database: `data_cache/football.duckdb` (32 MB, last written 2026-10-21), opened read-only via the
  DuckDB MCP server. `artifacts/db-hardening-20261023T143050Z/prod-snapshot.json` shows it is the
  same file the hardening work snapshotted (same 28 tables, same row counts).
- It is a **development copy**, not the published release asset. It predates the newer tables
  (`source_attempts`, `fpl_deadlines`, `player_codes`, `repair_log`, `migration_log` are absent).
- Code read: `src/fpl_agent/db/schema.py`, `database.py` (`name_link`), `ingest_fotmob.py`,
  `fpl_identity.py`, `eligibility.py`, `model/features.py` (`match_frame`), `model/players.py`,
  `pipeline.py`, `.github/workflows/data.yml`, perf baseline in the hardening artifacts.

### Baseline: coverage

| Area | Stored | Code supports |
|---|---|---|
| Seasons | 2026/27 only | backfill to 2017/18 (`backfill`, vaastav archive) |
| Competitions with matches | ENG1 (380, 70 finished), UCL, UEL, UECL | 15 FotMob competitions |
| Understat / football-data | EPL / E0 only, 70 matches each | 5 Understat leagues; E0, E1, E2 |
| FPL points | GW1–7, 4,551 rows, kickoff never null | all GWs |
| FPL squad snapshots | 2, both GW8 (2026-10-20) | one per run |
| FotMob detail | 162 matches, 6,862 appearances, 101,072 stat rows (28 stats), 4,603 shots | — |
| Odds | 70 matches, football-data avg/max, opening + closing; `match_odds_log` empty | Odds API, append-only log |
| `league_translation`, `transfers`, `reviews`, `lessons` | empty | populated by other jobs |

### Baseline: identity (the main finding)

| Measure | Value |
|---|---|
| FPL players with any FotMob link | 358 / 668 |
| FPL players with minutes > 0 that are linked | 293 / 449 (65%) |
| FPL minutes belonging to linked players | 96,781 / 137,657 (**70.3%**) |
| ENG1 minutes in FotMob vs in FPL | 138,169 vs 137,657 (the data is there; it is not joined) |

Recorded reasons a FotMob player was left unlinked (`ingest_state`, source `link`, 179 rows):
single-word name 82; position FWD vs MID 79; MID vs DEF 13; DEF vs MID 3; MID vs FWD 2.
A name that matches **no** canonical player is not recorded at all: `name_link` sets
`reason = None` when there are zero candidates (`src/fpl_agent/db/database.py:414`), so name-spelling
misses (e.g. "Levi Samuels Colwill" vs "Levi Colwill") are invisible.

Why it matters: `model/players.py` aggregates xG, xA and CBIT/CBIRT from `match_frame` by
`player_id`; FotMob rows of an unlinked player carry a different `player_id` from the FPL one, so
they never reach that player's rates.

### Baseline: freshness and decision-time evidence (local file)

- Last ingest 2026-10-20 20:09; six days old today.
- Squad snapshots exist only for GW8, so GW1–7 have no observed pre-deadline snapshot and by the
  rule in `eligibility.py` (observed snapshot holding ≥95% of scorers) cannot be `eligible`.
  `backtest --validated` therefore has nothing to validate on in this file.

### Baseline: ingestion efficiency

- Read workloads on the work snapshot (perf-baseline.json): player frame 0.09 s, minutes features
  0.65 s, match frame 0.03 s, quality checks 0.08 s, parquet export 0.36 s. None is a bottleneck.
- FotMob ingestion of 162 match pages took about 3 minutes (ingested_at 20:06:08 to 20:09:02); it is
  bound by the polite 0.4 s request delay and network, capped at 60 pages per competition per run
  (`pipeline.py:60`; the weekly workflow uses 120).
- Each source is already loaded inside one transaction, which is what DuckDB recommends for
  loops of INSERT statements: without it "every single statement will be wrapped in a separate
  transaction, meaning fsync will be called for every statement" ([DuckDB, Insert Statements](https://duckdb.org/docs/2.0/data/insert.html)).
  Batching the per-row inserts would save seconds at most.

## Ranked gaps

| # | Gap | Evidence | Value | Effort |
|---|---|---|---|---|
| 1 | 30% of FPL minutes have no FotMob detail joined | identity table above | High: rates for ~150 players, many highly owned, are priors only | Small–medium |
| 2 | Unlinked players with zero name candidates are not logged | `database.py:414` | Medium: the gap in #1 cannot be monitored without it | Small |
| 3 | Production coverage, freshness and eligibility not verified | local file is a dev copy; `gh` is not installed here | Unknown until measured | Small (read-only) |
| 4 | Non-EPL leagues and history absent locally, `league_translation` empty | coverage table | Medium for new signings, if also absent in production | Run existing jobs |
| 5 | Ingestion speed | perf numbers above | Low | — |

## Recommended slices (in order)

### S1. Link audit, read-only (do first; it is the acceptance metric for S2)
Add a read-only report (e.g. `db-links` CLI or a section of `source-health`) giving, per season:
FPL players with minutes, how many are linked to FotMob, linked minute share, and the unlinked list
with reason. Also record the zero-candidate case in `ingest_state` with reason `no name match`.
- Acceptance: on `data_cache/football.duckdb` the report prints 293/449 and 70.3% (± rounding);
  a unit test with a synthetic DB covers each reason including `no name match`; the DB file hash is
  unchanged after running the report.

### S2. Appearance-evidence linker (the main improvement)
Link an unlinked FPL player to an unlinked FotMob player only when:
- they share at least 2 matches (FPL `fixture_id` -> `match_ids` source `fpl` -> `match_id`) where both
  played and minutes agree within ±1;
- those agreeing matches are at least 80% of the FPL player's appearances; and
- exactly one FotMob candidate meets this (otherwise leave unlinked, recording "ambiguous").

Names and positions are not used as evidence, consistent with the identity rule in
`fpl_identity.py:201`. This is a deterministic, conservative version of the Fellegi–Sunter link /
non-link / possible-link decision, where uncertain pairs are held back rather than merged
(Fellegi and Sunter, "A Theory for Record Linkage", *JASA* 64(328):1183–1210, 1969, DOI
10.2307/2286061; [copy](https://courses.cs.washington.edu/courses/cse590q/04au/papers/Felligi69.pdf)).

Store links as reversible evidence rather than rewriting history: e.g. a `player_links` table
(`source`, `from_player_id`, `to_player_id`, `evidence` JSON with matches and minutes, `recorded_at`,
`rolled_back_at`) applied when `match_frame` reads, or, if rows are re-pointed, journalled in the
same transaction like `migration_log`/`repair_log`. Run it on a copy first.

Measured on the local file with that exact rule (read-only query): 127 of 156 unlinked players get a
unique candidate, covering 34,483 of 40,876 unlinked minutes. A 25-pair random sample was checked by
hand: all looked like the same person (e.g. Murillo Costa dos Santos = Murillo, Levi Samuels
Colwill = Levi Colwill, Igor Thiago Nascimento Rodrigues = Igor Thiago, Saka = Saka).
- Acceptance: linked minute share on a copy of the local file rises from 70.3% to ≥ 95%; zero links
  where two candidates qualify (test); a link is undone exactly (test); a synthetic case of two
  substitutes with identical minutes in one match is not linked; `pytest -q` passes; the
  `leakage-reviewer` agent signs off on how `match_frame` applies links (identity is time-invariant,
  but links found from later matches must not change which rows a past cutoff sees).
- Optional follow-up: show the projection change for newly linked players (e.g. GW8 xpts before and
  after) so the gain is visible, not assumed.

### S3. Verify production, read-only
Download the latest published release asset (data.yml / `scripts/data_restore.sh`) into a scratch
folder and run `db-schema`, `source-health`, `db-eligibility --season 2026` and the S1 report on it.
- Acceptance: the four outputs are saved as an artifact; decide on gaps 3–4 from them.

Not recommended now: changing the database engine, adding a new data source, or optimising inserts.
None of them addresses an observed gap.

## Unknowns

- Contents of the published production database (seasons, leagues, snapshot cadence, eligible GWs):
  not accessible here (`gh` not installed). S3 answers this.
- Whether production shows the same 70% linked share; the cause (`name_link` rules) is in code,
  so it very likely does, but it has not been measured.
- The precision of S2 beyond the 25-pair sample; S2's tests and a full review of the 127 pairs on a
  copy should confirm it before it runs in the pipeline.
- One-appearance players (19 of the unlinked) cannot meet the 2-match rule; they stay unlinked
  until they play again.

## Sources

- [DuckDB documentation, Insert Statements (data import)](https://duckdb.org/docs/2.0/data/insert.html)
- Fellegi, I. P. and Sunter, A. B. (1969), "A Theory for Record Linkage", *Journal of the American
  Statistical Association* 64(328), 1183–1210. [PDF copy](https://courses.cs.washington.edu/courses/cse590q/04au/papers/Felligi69.pdf)
- Repository code and the local database, as listed under "What was measured".

## Implementation results (FAA-4 to FAA-6, corrected after audit FAA-9 to FAA-11, 2026-10-26)

All figures come from copies of the development database. Production was not touched, and whether
it has the same gap is unmeasured (FAA-7). Evidence: `artifacts/db-links-20261026/r2/` (corrected
rules). The first run's files in `artifacts/db-links-20261026/` are kept as history. Audit:
`docs/research/database-link-audit.md`.

| Measure (2026/27, dev copy) | Before | After applying the reviewed links |
|---|---|---|
| FPL players who played, linked to FotMob | 293 / 449 | 424 / 449 |
| FPL minutes linked (report as of now) | 96,781 / 137,657 (70.3%) | 134,078 / 137,657 (97.4%) |
| GW9 projection made now: players on their own PL rates / on position priors | 277 / 364 | 399 / 233 |
| Projection at 2026-10-20 20:00 (before `valid_from`) or 2026-10-24 (before the links were written) | - | 0 players changed |

When a link may be used (exploratory and validated runs alike, there is no looser mode):
- Evidence counts at a time only if it happened and was stored before then. That means kickoff
  and `ingested_at` for FPL rows, `ingested_at` for FotMob rows, and observed squad snapshots for
  teams; reconstructed archive snapshots never count. A row with no stored time never counts, and
  a corrected or backfilled row is unknown before its new time.
- A decision uses a link only if the link was written (`recorded_at`) and its evidence was valid
  (`valid_from`) by the decision time. Every one of these links was written on 2026-10-26, so no
  past gameweek's backtest uses them; only decisions from now on do.
- A link the evidence stored by the decision time no longer supports is not used
  (`link_candidates.revalidate`). It is listed by `db-links` (`unsupported_links`) and by the
  quality checks (`player_link_unsupported`). On the dev copy today, none are unsupported.
- FPL/FotMob conflicts are checked on linked records too (`quality.source_conflicts`,
  `linked_record`), so validated runs exclude those matches. On the dev copy none of the 3
  conflicts involve a linked record.
- Coverage counts only links made from that season's evidence. The reader credits a linked record's
  rows from any season, because identity does not change; the timing rules above still apply.

Proposals: 131 accepted, 25 rejected (19 with one appearance, 3 with one stat contradiction, 2
ambiguous, 1 below threshold). These are the same pairs as before the correction; every one is
valid from 2026-10-21, the day after the first observed squad snapshot. All 131 were checked
against both names. The two whose names share no word were checked against first-party fields:
- FPL's own `web_name` is "Costinha" for player 132 (João Pedro Loureiro da Costa, FPL code
  451490), matching FotMob record 1079456 "Costinha".
- FPL's `web_name` is "Beto" for player 289 (Norberto Bercique Gomes Betuncal, code 486385),
  matching FotMob record 1079485 "Beto".

Both pairs also agree on side in every shared match (6 of 6 and 2 of 2).

Undo: `--undo` restores every reader's and report's view of the data exactly (GW9 projections
after undo are identical to the original). It does not restore the file. The rolled-back rows stay
in `player_links` as the record, and an apply on an older file also creates the newer empty tables.
Batch ids are unique, so apply, undo and apply again keep every batch on record.

Remaining gaps:
- 25 played players are still unlinked (3,579 minutes), most with one appearance.
- `player_ids` has no history. Who holds which id is read as it is now; this can only remove
  candidates, never add one.
- `fit-translations` reads the unresolved frame. This is correct for its purpose (FotMob records
  already span competitions).
- Five name-linked players have an FPL team that differs from their FotMob Premier League team.
  They predate this work and are out of scope here.
