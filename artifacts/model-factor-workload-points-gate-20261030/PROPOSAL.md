# Comparison plan r4 for `minutes-workload-v0`: points gate

- **Owner:** Nabil, 2026-10-30.
- **The one plan file for Wegz's review:** `comparison-plan-r4.json`. If it passes, this
  folder lands as its own versioned package (Sami, PR #56).
- **Supersedes r2** for any future comparison. r2 is the frozen package in
  `artifacts/model-factor-workload-20261030/`, sha256 48cb7e26.
- **Nothing was run or tuned.**

## Files

| File | What |
| --- | --- |
| `comparison-plan-r4.json` | The plan |
| `r2-to-r4.diff` | r2 → r4 (`diff -u --strip-trailing-cr`, since r2 uses CRLF line endings) |
| `r3rev2-to-r4.diff` | Changes since r3 rev 2, which Wegz has not reviewed |
| `wegz-review.md` | Wegz's review of r3 rev 1 (Wegz's file) |
| `superseded-r3rev1/`, `superseded-r3rev2/` | Earlier drafts, kept for the trace. `superseded-r3rev1/r2-to-r3.diff` was re-made with new path headers, so its hash differs from the 6fd839db Wegz checked; the content is the same |
| `hashes.sha256` | Hashes of every file in the folder |

## Why a points gate

- **The gap in r2:** r2 judges only start and 60+ minutes log loss, so a minutes gain could be
  presented as a points gain without evidence.
- **Expected points (xP) error is now a gate for promotion.**
- **Team points are reported only.**
  - Weekly team scores vary by about 15–20 points.
  - A plausible effect is under 1 point a week, so a few gameweeks of team points are noise.

## What r4 decides

### 1. Training rule (god signed off option (b), Wegz F4)

- **Shared minutes features:** both arms train on the same full history visible at the
  evaluated gameweek's cutoff C_g, as production `sim-v1` does. The baseline **is** production
  `sim-v1`.
- **Workload inputs:**
  - The 7 `obs_` values plus `linked` and `obs_link_suspect`.
  - On a training row from gameweek t, they hold `obs_` at C_t only if t is eligible,
    repair-proven (`repair_log` present) and before g. They are NULL everywhere else.
  - `rec_` values are never used on this route.
- **Stated limitation:** this is an intentional risk that workload goes missing in training but
  not in use, and it is not historical evidence.
  - In training, workload is present only on recent rows. At evaluation it is present for every
    trusted linked player.
  - So whether workload is present goes with how recent the row is, and the model could learn
    recency instead of workload.
- **Disclosure:**
  - the count of non-NULL workload training rows for each evaluated gameweek;
  - a separate paired result on rows whose workload is NULL at evaluation (unlinked or
    link-suspect players). On those rows any change can only come from this skew.

### 2. Exact paired regime (F3)

- **Gameweeks:** each evaluated gameweek g is taken in order.
- **Cutoff:** C_g is the official deadline from `eligibility.gameweek`.
- **Refitting:** both arms are refit at every evaluated gameweek (`refit_every=1`) on the same
  training rows.
- **What differs:** only the workload columns.
- **What is shared:**
  - the strength ratings;
  - the odds, using only odds known at the cutoff;
  - the player pool;
  - the simulation configuration;
  - the removal of excluded matches.
- **One reviewed training hook** is used by both arms. Without it the result is exploratory and
  cannot gate promotion.

### 3. Sample minimums, held separately (god)

- **Workload training before the first evaluated gameweek:**
  - at least **3** eligible, repair-proven gameweeks;
  - at least **1,200** trusted linked rows with non-NULL `obs_` workload. GW7–8 had about 480
    trusted linked players each.
- **Evaluation:**
  - at least **3** eligible, repair-proven gameweeks;
  - the r2 minimums, which are unchanged;
  - 1,500 player-gameweek rows for points.
- **Earliest claim:**
  - This needs **6 eligible gameweeks**.
  - If GW9 is the first eligible gameweek, that is after GW14 is scored. A later start moves it
    by the same number of gameweeks.
  - FAA-41 must be live and recording deadlines and `repair_log` before each deadline.

### 4. Points metric and cohorts (F1)

- **Unit:** player × eligible gameweek. The sim's xP is compared with official
  `total_points`, the `projection_backtest` unit.
- **Metric:** primary is RMSE (root-mean-square error); MAE and bias are reported.
- **Decision-relevant cohort:** `fpl_players_gw.now_cost >= 50` (stored in tenths of a million), meaning a price of
  5.0 or more.
  - It is read from the player's row in the decision snapshot: the observed bootstrap pull
    `eligibility.gameweek` uses, with `snapshot_at` ≤ C_g.
  - It is fixed at the cutoff, and neither arm produces it.
  - In the 2026 GW8 snapshot it covers 323 of 668 players.
- **Secondary cohort, reported only:** last-5 points per fixture ≥ 3.0.
- **Also reported:** each position, and the r2 cohorts.

### 5. Uncertainty (F2, F3)

- **Sampling error:** a paired bootstrap that resamples **players as clusters**, with all
  their evaluated gameweeks: 2000 draws, seed 20261030.
- **Simulation error (MCSE, the Monte Carlo standard error):**
  - Each arm runs with seeds 20261030 and 20261031, and every metric is the mean of the two
    runs.
  - The MCSE of each ratio is |r₁ − r₂| / 2 and is reported next to it.
  - **Tolerance:** the MCSE of the all-rows ratio must be ≤ 0.001, one fifth of the 0.005
    margin. If it is not, the simulation sample count n is doubled from 800 and both seeds are
    run again.

### 6. Gates and claims (F5)

- **Promotion gate (non-inferiority, on point estimates):**
  - xP RMSE ratio ≤ 1.005 on all rows;
  - ≤ 1.01 on the price cohort;
  - ≤ 1.01 on each position with at least 300 rows;
  - plus every r2 condition.
- **Improvement claim:**
  - RMSE reduction ≥ 1%;
  - the bootstrap 95% upper bound is below 0;
  - the price cohort ratio is ≤ 1.0;
  - the leave-one-out rule below holds.
- **Leave-one-gameweek-out, for any number of folds:** with k evaluated gameweeks there are k
  folds, each dropping one. The sign must hold in at least ⌈2k/3⌉ folds: 2 of 3, 3 of 4,
  4 of 5, 4 of 6 or 5 of 7. The same rule now applies in `acceptance_rule`.
- **What each outcome allows anyone to say:**
  - Minutes pass, points gate fails: "no points benefit shown". Not promoted.
  - Minutes pass, points non-inferior: "improves minutes forecasts with no points harm detected
    (point estimate within margin)".
  - Only the improvement bar allows "improves expected-points accuracy".
  - Replay team points never support "scored more points".
  - Exploratory or reconstructed gameweeks support no claim.

### 7. Team points (unchanged from r3)

- **Run:** `replay_season(validated=True)` once per arm, with the same configuration, starting
  squad, chips and gameweeks.
- **Status:** reported only, labelled "descriptive; not evidence of a points gain".

## Unchanged from r2

- the candidate definition (the baseline now names the same code revision as the candidate, Wegz U2);
- the evaluation rows;
- the minutes metrics and their 2% bar;
- the r2 cohorts and the 1% limits for the NULL cohorts;
- the known limits;
- the exploratory route.
