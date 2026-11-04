# Point-in-time evaluation, feature overlays and the weekly learning loop

This page covers three things: how forecasts are recorded and scored point in time, which
feature overlays exist and which of them change the plan today, and how the agent decides,
week by week, whether a change has earned its place.

News and social ingestion (RSS, X and claim extraction) is **not part of the agent**: it was
built and then left out on 4 Nov 2026 by the owner's decision. The overlay registry and the
learning loop are generic, so a future feature plugs in the same way the workload overlay does.

## What is active and what is shadow-only

| Piece | Status by default | Changes the plan? |
|---|---|---|
| Pre-deadline review, cutoff recording, final-only calibration and lessons | **active** | Yes. Calibration and lessons now learn from final, eligible reviews only. |
| Rest and workload overlay (`FEATURE_WORKLOAD`) | **shadow** | No. A matched experiment arm with it is stored and scored. |
| Learning state (`fpl-agent learn`) | **active** in the review workflow | Only through calibration factors and feature promotions that passed their thresholds. Until a state exists, the plan behaves as before. |

Set `FEATURE_<NAME>=off|shadow|on` (as an environment variable or repository variable) to
override. An environment value always wins over the learning state. Live FPL execution, the
rails and the deployment settings are unchanged: overlays only change the probabilities the
simulator uses, and FPL's own status flags still decide hard eligibility.

## Point in time: deadlines, cutoffs, finality

* Every `db-update` (and `lessons`) records each gameweek's FPL `deadline_time`, `finished` and
  `data_checked` in `fpl_events`.
* Every stored projection run is registered in `forecast_runs`: `created_at`, the information
  `cutoff` it used, the first gameweek's `deadline` (and whether that came from FPL or was
  estimated as 90 minutes before the first kickoff), `eligible` (made before the deadline),
  `variant` (`primary` = the raw forecast the plan started from, `issued` = the forecast
  exactly as the planner received it, `experiment` = a matched experiment arm), the parent run,
  the model and feature versions, the full configuration (feature modes, features applied,
  calibration, learning version, samples, seed), and any point-in-time limitations.
* A review scores **one** run: the newest eligible primary run. It never mixes runs per player,
  and a run made after kickoff can never be scored. Projections saved before this change have
  no registry row; they are grouped by run id and judged against the deadline the same way
  (`legacy: true` in the report).
* A gameweek is **final** when FPL marks it `finished` and `data_checked` and its points are
  loaded. Provisional reviews are stored with `final: false`; provisional lessons are reported
  with a PROVISIONAL header and **not stored**. Calibration, the template correction and the
  learning state ignore anything that is not final and eligible.
* A player in the forecast with no official result row is **left out** of the review and
  counted in `missing_actuals`. It is never scored as zero.
* A double gameweek's actuals sum both fixtures, and the scoring components come from both
  fixtures' `explain` breakdowns.

What each input of a projection may see at cutoff `c`:

| Input | Rule |
|---|---|
| Squad snapshot (status, chance, price) | `snapshot_at <= c` |
| Bookmaker prices | pre-match prices with `ingested_at <= c`; closing prices never before kickoff |
| Matches and team strength | matches before the cutoff's date |
| Player history and minutes model | gameweeks before the one being forecast |
| Workload | appearances whose kickoff + 3 h <= c |

**Historical backfills are not point-in-time**, and backtests report this under `limitations`:

* vaastav archive squad rows are stamped at kickoff and carry no availability. A backtest uses
  them (prices at kickoff, everyone assumed available) and says so.
* Pre-match odds loaded by a later backfill have an unknown publication time. They are used only
  when no point-in-time price exists. Closing prices are never used before kickoff.

## Feature definitions

### Rest and workload (`model/workload.py`)

| Feature | Unit | Unknown (NaN) when |
|---|---|---|
| `xc_rest_days` | days since the last appearance with minutes, any competition | none in the previous 60 days |
| `xc_mins_7d`, `xc_mins_14d` | minutes in the previous 7 / 14 days | the player has no appearance rows in the previous 365 days |
| `xc_team_matches_7d` | club matches in the previous 7 days | the club has no matches in the previous 60 days |

* **Cutoff:** facts of a match exist from kickoff + 3 h. At inference the run's cutoff applies
  as well. Training rows use only matches before their own kickoff.
* **Fixture time:** each player is described at his own club's kickoff (a Monday club gets
  Monday, not the gameweek's first Saturday kickoff). In a double gameweek it is the club's first
  fixture, and both fixtures share that forecast. The plan and the walk-forward use the same
  rule, and a test checks that they produce the same workload inputs.
* **Sources:** `appearances` and `matches` (FotMob, 15 competitions including UCL, UEL and
  UECL). Domestic cups are not in the database.
* **Training and inference:** a second gradient-boosted P(start) classifier is fitted on the
  baseline features plus these. NaN is kept as missing, never set to zero. The overlay moves
  next-gameweek P(start) toward it by at most +-0.10, and a player with no workload coverage
  keeps the baseline.

## Expected run cost

| What | When | Requests or cost |
|---|---|---|
| Experiment arms | every plan run | next gameweek only: one control arm plus one arm per feature that is not `off` (two arms with the workload feature enabled), each with the plan's sample count and seeds and its fitted models; the issued forecast is stored once more. About 20-40% extra run time. |
| Evaluation and learning | Tuesday review (`review.yml`) | local computation only |

## The weekly learning loop

Run by `review.yml` after the data update: `fpl-agent evaluate --gw 0` then
`fpl-agent learn --gw 0`.

1. **Score what was issued.** For the final gameweek, take the newest eligible primary run.
   Score its `raw` forecast (what calibration learns from) and its `issued` variant (what the
   planner received after calibration and the template correction, with both transformations
   recorded). Metrics: points MAE, RMSE and bias; P(start) Brier, log loss and a 5-bin
   calibration table (players with one fixture, `starts` from FPL); P(play) Brier.
2. **Matched experiments.** Each plan run also stores, for its first gameweek, experiment arms
   simulated with the same fitted models, sample count and seeds: `control` (the issued
   configuration again: the null intervention, which must reproduce the primary's expected
   points exactly or every arm of the run is invalid), `with:<feature>` for each shadow feature
   and `without:<feature>` for each feature that is `on`. A feature's comparison is the pair
   that differs in that feature only, so evidence keeps coming after promotion (control vs
   `without`) as well as before it (`with` vs control), in every off/shadow/on combination.
   Each arm is validated against the primary: made before the deadline, cutoff no later than
   it, same season and gameweek, kind, model, model and feature versions, samples and seed,
   null check passed. Anything else is listed as `excluded` and supplies no evidence.
3. **Evidence.** For each comparison, `affected` = players the feature changed in its arm. The
   change in absolute points error and the change in P(start) Brier are both computed on those
   same players. A feature's evidence pools the comparisons of its current mode since that mode
   last changed.
4. **Thresholds, fixed in `learning.THRESHOLDS` before any evaluation:** a shadow feature is
   promoted to `on` only with >= 4 final gameweeks, >= 60 affected player-weeks, a mean
   improvement larger than 2 standard errors, and a Brier change on the affected players that is
   present and not worse. An `on` feature whose removal would improve the error by more than 2
   standard errors goes back to `shadow`. Missing metrics never change a mode.
5. **Candidate vs current.** "Current" is the configuration the agent really runs with
   (`agent.active_config`): the state's factors, or, with no learning state yet, the factors from
   final reviews. Calibration factors are re-estimated from final, eligible reviews, each at
   most 0.03 from that effective current calibration, and nothing changes before two final
   reviews. The holdout compares the factors the holdout week's forecast was issued with (its
   recorded configuration) against the candidate. The candidate's calibration is validated on the newest final week, estimated
   *without* that week. With no holdout measurement the calibration is kept. It becomes current
   only if its holdout MAE is no more than 0.01 worse; otherwise it is stored as `rejected`.
6. **Exactly once per evidence.** Each update records an evidence key in `learning_updates`:
   a digest of the evaluations and final reviews up to that week, its official results, the
   versions and the thresholds. Running it again with the key that week was last processed
   with changes nothing, so
   calibration cannot compound and a rollback is never undone by a repeated update. A different
   key for the newest processed week (FPL corrected the results, or a correction was reverted
   to evidence seen before) is a correction: if the state
   that week produced is still current, the candidate is recomputed from the state before it
   (one weekly step, not two); otherwise (rolled back, or a later week processed since) the
   correction is recorded and the state is left alone. An older week processed out of order
   changes nothing.
7. **Versioned state.** Each row of `learning_state` has its version, parent, status (`current`,
   `retired`, `rejected`, `rolled_back`), the gameweek it covers, the training cutoff, the config
   (calibration, feature modes, and the week each mode last changed), sample counts and the
   full evaluation, thresholds included.
8. **Rollback.** `fpl-agent learn --rollback` makes the parent version current again, or the
   built-in defaults if there is none (default modes, calibration from final reviews as before
   this change). `--rollback --to N` restores version N. The next plan run uses it: its
   `forecast_runs` row shows the calibration, modes and learning version it ran with.
   `fpl-agent learn --history` lists every version.

Hindsight lessons about transfers (`lessons.py`) are kept apart. They tune the planner's
memory switches as before, but they never count as evidence that a prediction feature works.

**Historical walk-forward** (`fpl-agent walk-forward --season 2025 --from-gw 6 --to-gw 38`) is
the chronological check for the workload feature, which does have history. Every test forecast
is made the way the agent forecasts at a deadline (`learning.walk_forward_rows`): models fitted
on outcome rows of earlier gameweeks and squad snapshots observed by the refit gameweek's
deadline, and reused until the next refit, while the inputs of every forecast are rebuilt from
the history available at its own deadline (a result that became final since the refit counts);
each player scored once from his history before the deadline, so both fixtures of a
double gameweek get the same forecast and the second never sees the first's result; squad rows
as observed by the deadline (an archive row is stamped at kickoff, so the previous gameweek's
row is used); workload facts public by the deadline, and for training rows by their own
gameweek's deadline. Changing any result or snapshot after a deadline cannot change that
gameweek's forecasts (tested). The first half of the gameweeks chooses the bound (0.05, 0.10 or
0.15); the second half is the final test, scored once with that bound. It also runs feature
ablations (all workload features, rest only, no club congestion). It is minutes-level (P(start)
Brier and log loss). Points-level comparisons need `backtest`.

### Measured: 2025/26 workload walk-forward (rerun after the final corrections, supersedes all earlier figures)

* Command: `learning.walk_forward(db, 2025, list(range(6, 39)), refit_every=4)` (the same as
  `fpl-agent walk-forward --season 2025 --from-gw 6 --to-gw 38`), on 4 Nov 2026.
* Dataset: the `data` release asset `football-db.tar.zst`, sha256
  `d3cc931b3e133b9f89eb2ad6332af26b64b8ca7ea1b5e7970a371eaec46f9814` (236,101 FPL points rows,
  315,463 squad snapshots, 1,751,724 appearances, 51,234 matches).
* Tuning GW6-21, final test GW22-38: 13,935 player-fixtures, workload known for 100%.
* P(start) Brier on the test weeks (lower is better): baseline 0.07527; all workload features
  (bound 0.05) 0.07522; rest only (0.05) 0.07529; without club congestion (0.10) 0.07532.
  Log loss 0.24251, 0.24260, 0.24261, 0.24282. With all features the Brier was lower in 11 of
  17 test weeks, mean change -0.00005 per player-fixture with a weekly standard error of
  0.00017: **no measurable effect**.
* Why the numbers moved: the previous run (baseline 0.09853, all 0.09719) reused the refit
  gameweek's history for up to three later gameweeks, so the baseline's rolling inputs were up
  to three weeks stale. Rebuilding inputs at every deadline improves the baseline a lot, and the
  apparent workload gain mostly disappears: it was largely compensating for stale history. The
  first figures reported (0.07508 / 0.07520) came from a version that leaked post-deadline data.
  Both earlier sets are withdrawn.
* Limits: one season, one tuning/test split, minutes only (no points-level result), domestic
  cups missing, archive squad rows from the previous gameweek. The feature stays in shadow.

## Commands

| Command | What |
|---|---|
| `features` | the feature modes in force and where each comes from (env, learning state or default) |
| `evaluate --gw N` | score the issued forecasts of a final gameweek |
| `learn --gw N`, `learn --rollback [--to V]`, `learn --history` | weekly update, rollback, version list |
| `walk-forward --season S --from-gw A --to-gw B` | historical chronological evaluation of the workload feature |

## Schema changes (additive)

New tables: `fpl_events`, `forecast_runs`, `evaluations`, `learning_state`,
`learning_updates` and `schema_migrations`. One column was added: `projections.variant` (migration
`2026-11-projection-variant`, `ADD COLUMN IF NOT EXISTS`). An older DuckDB file upgrades when
it is opened. Parquet import now matches columns by name, so an older export still restores
(new columns stay NULL, missing tables stay empty). Every new table is exported with the rest.
