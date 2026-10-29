# M02 candidate handoff: FPL expected-goals/assists fallback

Owner: Nabil (nabil-mvsc2c3l). Reviewer: Wegz (FAA-15). Date: 2026-10-29.
**Recommendation: do not promote. Keep the code as an experiment, switched off by default (or
reject it).** No validated evidence exists: 0 eligible gameweeks. The exploratory result fails the
predeclared 2% rule on both primary metrics.

## Start fingerprint and files

- Git HEAD `c6a0c5f6`, branch `task/phase1-dashboard`, dirty tree (other owners' changes kept).
- `start-fingerprint.txt`: `players.py` b7f477ee… (the inherited candidate), `features.py`
  22c5abf5…, `tests/test_players.py` a86f3764….
- **Changed by Nabil:**
  - `src/fpl_agent/model/players.py`: explicit `fpl_xg_fallback: bool = False` switch, docstring,
    formatter reflow. Now 9b8a2a15…; diff `players-nabil-vs-inherited.diff`.
  - `tests/test_players_fpl_fallback.py`: new, d427fc02….
- **Inherited and unreviewed (now owned by Nabil):**
  - The fallback logic in `players.py`, written by zaza-mvpmyn3b at 2026-10-27 16:46 UTC before
    the stop. It also adds the `xg_source` and `xa_source` output columns. Full change against
    HEAD: `players-vs-HEAD.diff`.
  - The `stats_ingested_at` column in `features.player_gw_frame` (`features.py` changed after M00
    too; I did not edit it).
- Not touched: `dashboard/service.py`, `project.py`, production data and config. No caller turns
  the switch on (`project.py:278` calls `fit_player_rates` without `as_of` or the switch).
  The evidence is narrower than "pipeline unchanged":
  - Verified: switch off reproduces the original M00 rate columns byte for byte.
  - Not verified by a run: the projection and saved output. The inherited
    `stats_ingested_at` column in `features.py` and the added `xg_source`/`xa_source` rate
    columns still need review. By reading only: `project.py:283-284` maps just `source` and
    `evidence_minutes`, so the new columns should not reach saved projections.

## Model identifier (for Pablo's import filtering, contract r3)

- **Baseline:** `MODEL = "sim-v1"` (`project.py:24`). `save_projection(..., model=MODEL)`
  (`project.py:375`) writes it to `projections.model`; the only caller is `agent.py:328`, with the
  default.
- **Enabled M02:** today it **cannot be produced by the pipeline and has no identifier**:
  `build_projection` neither takes nor passes the switch. If it is ever wired in, it must be saved
  under a distinct id (proposal: `sim-v1-fplxg`), never `sim-v1`. Per row, `detail.rate_source`
  would also show `"fpl"` for players using the fallback. Not implemented: the candidate is
  frozen.

## What the GW5–7 comparison is

**An oracle-conditioned share diagnostic, not deployable forecast performance.** Two different
data roles (line numbers refer to `m02_compare.py`):
- **Fitting inputs (no target data):**
  - Lines 115–116: copy the FPL rows and set ingestion time = kickoff.
  - Line 121: keep only rows with kickoff before GW k's first kickoff (= GWs 1..k−1, all
    finished).
  - Line 79: FotMob rows limited to dates before the cutoff date.
  - Line 122: baseline and candidate are fitted on the same rows.
- **Scoring exposure (target-match realised values, applied equally to both):**
  - Line 119: GW k realised minutes.
  - Lines 124–125: the team's realised FotMob xG in that match.
  - Lines 139–142: λ = share × finish × team xG × minutes/90.
- A real forecast would have to predict minutes and team xG. These numbers therefore compare
  only the share estimates, under known exposure. They say nothing about expected points or
  decisions.

## What the candidate does

- **Which players:** for a player with no usable detailed (FotMob) exposure for a stat, it uses
  FPL per-fixture xG or xA instead of the position prior.
- **How:** the FPL value is converted to a team-xG share with the league-mean PL team xG, then
  shrunk with the same 12 team-xG pseudo-exposure.
- **Per stat:** goals and assists are handled separately, and are used only where detailed
  exposure is 0, so the two sources are never added together.
- **Cutoff:** only FPL rows that were ingested by the cutoff and kicked off before it are used.
  Without team exposure it fails closed.
- **Unchanged:** scoring rules and minutes logic.

## Checks (exact commands, real results)

- `.venv/Scripts/python.exe -m pytest tests/test_players.py tests/test_players_fpl_fallback.py
  tests/test_point_in_time.py tests/test_validated_eval.py tests/test_project.py
  tests/test_backtest.py tests/test_replay.py -q -p no:cacheprovider` → **66 passed**.
- `ruff check` on both changed files → clean.
- New tests cover:
  - switch off, or on without a cutoff, equals the baseline
  - only players with no detailed history change
  - **cutoff invariance:** rows after the cutoff are rewritten and the result does not change;
    rows kicked off before the cutoff but ingested after it are ignored
  - goals and assists are independent
  - fails closed without team exposure
- Full suite (814 tests) **not run**.
- `m02_compare.py` was run twice (`run-1.log`, `run-2.log`). The work copy hash was unchanged
  before and after (94f68833…). The two outputs agree to 1.3e-13 relative; counts and labels
  match exactly.

## Results (`m02-comparison.json`)

**A. Baseline switch:** current code with the switch off reproduces M00 byte for byte
(ff69d7b0…13f5) on the original columns.

**B. Live candidate at the M00 cutoff (point in time; nothing to score it against):**
- 118 of 668 players change.
- Fallback used for goals by 111 players and for assists by 79; the prior-only count falls from
  233 to 208.
- All 344 players with detailed goal history are identical to the baseline.
- Changed players by position: DEF 48, MID 44, GKP 14, FWD 12.
- Median change in goal share for changed players: ×0.97.
- The `source` label moves 98 players from `pl` to `fpl`. They had PL minutes but FotMob xG was
  missing (FotMob xG covers about half of PL rows).

**C. EXPLORATORY reconstruction, GWs 5–7:**
- Method: FPL rows are treated as known at kickoff, although they were really ingested
  2026-10-20. Minutes and the team's realised FotMob xG are taken as known, to isolate the share
  estimate. Matches 31, 40 and 62 are excluded.

| Cohort | Rows | Players | Goals | Assists | Goal deviance change | Assist deviance change |
| --- | --- | --- | --- | --- | --- | --- |
| All rows | 890 | 396 | 84 | 79 | −0.56% (better) | +0.13% (worse) |
| Goal fallback applied | 408 | 195 | 34 | 36 | −1.45% | +0.47% |
| Assist fallback applied | 337 | 160 | 30 | 31 | −0.50% | +0.34% |
| Unchanged, detailed history | 428 | 188 | 47 | 43 | 0 | 0 |
| DEF | 297 | 134 | 14 | 20 | −1.67% | −0.72% |
| MID | 426 | 195 | 45 | 48 | −0.24% | +0.44% |
| FWD | 108 | 46 | 25 | 10 | +0.53% | +1.72% |

- Calibration slope, all rows: goals 0.88 → 0.88, assists 0.80 → 0.78. Both lie in the
  0.8–1.2 range except assists under the candidate.
- No position cohort reaches 50 events, so the regression limit cannot be tested.

**Against the predeclared rule:**
- Eligible gameweeks: 0 of 3 → fail.
- Both primary metrics must improve by at least 2%: goals −0.56%, assists +0.13% → fail.
- Assist events: 79 overall, ≥50 → pass.

Verdict: **experimental / not promotable.**

## Leakage boundary in the reconstruction (for Wegz; raised by god 2026-10-28)

- **Only ingestion time is relaxed, and only for completed earlier fixtures.** For scored
  gameweek k:
  - the cutoff is the first kickoff of GW k
  - the fit uses only FPL rows with kickoff before the cutoff, i.e. GWs 1 to k−1
  - FotMob rows are limited to dates before the cutoff date
  - the target gameweek's own xG/xA and results never enter features or fitting.
- Checked by `leakage_check.py` → `leakage-check.json`:
  - 0 rows from GW k or later in any fit set.
  - Latest fitted kickoff before each cutoff: 96 h (GW5), 476 h (GW6), 112.5 h (GW7), so every
    fitted match had finished.
- **Source rows:** none were overwritten. `stats_ingested_at = kickoff` is set on an in-memory
  copy, and the database hash is unchanged.
- **Real-ingestion cutoff invariance** is kept in the code and covered by
  `test_rows_after_the_cutoff_cannot_change_the_result`.
- **Remaining look-ahead, disclosed:**
  - Positions and teams come from the GW8 snapshot.
  - Identity links were recorded 2026-10-26 and are applied through `player_links.resolve` at
    the cutoff.
  - Scoring uses realised minutes and realised team xG as an oracle exposure. This is evaluation
    design to isolate the share; it is not a model input, and it applies equally to baseline and
    candidate.

## Limitations

- The denominator is the league-mean team xG, not the player's own team. This biases shares for
  players at strong or weak teams.
- Live use depends on `stats_ingested_at`. All 2026 FPL rows were ingested on one day, so any
  honest past replay equals the baseline.
- Reconstruction uses the latest-snapshot team per player (transfers ignored) and a realised-xG
  oracle. It is not a test of decisions or of total expected points.
- 3 gameweeks and fewer than 100 events: differences of about 1% are inside noise.

## Proposed next experiment (needs a separate contract)

- **Team denominator:** use the player's own team PL xG in the same fixtures as the denominator.
- **Re-test on eligible data:** re-test once eligible gameweeks exist, which needs real
  point-in-time ingestion from now on; a fresh snapshot cannot backfill eligibility.
- **Larger past-season reconstruction:** a separately contracted reconstruction over previous
  seasons would add more data.
- **Historical replay:** repairing the `team_id` replay failure stays out of scope.
