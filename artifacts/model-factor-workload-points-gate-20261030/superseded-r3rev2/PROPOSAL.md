# Comparison plan r3 for `minutes-workload-v0`: points gate (rev 2)

- **Owner:** Nabil, 2026-10-30.
- **Status:** rev 2, after Wegz's review (`wegz-review.md` 70ece5dc, CHANGES NEEDED, F1–F5).
- **Sign-offs needed:**
  - Wegz must review again;
  - F4 changes a rule from r2, so god must sign it off.
- **Nothing was run or tuned.** The r2 package in `artifacts/model-factor-workload-20261030/`
  is unchanged; r2 is 48cb7e26.
- **Landing:** if passed, this folder lands as its own versioned package (Sami, PR #56). It
  supersedes r2 for any future comparison.

## Files

| File | What |
| --- | --- |
| `comparison-plan-r3.json` | The r3 plan (rev 2) |
| `r2-to-r3.diff` | r2 → r3 rev 2 (`diff -u --strip-trailing-cr`, since r2 uses CRLF line endings) |
| `r3-rev1-to-rev2.diff` | What changed in answer to Wegz |
| `wegz-review.md` | Wegz's rev 1 review (Wegz's file) |
| `superseded-rev1/` | The rev 1 Wegz reviewed: `PROPOSAL.md` 9956e29e and the plan 9b17a9e1. Its diff was re-made here with new path headers, so its hash differs from the 6fd839db Wegz checked; the content is the same |
| `hashes.sha256` | Hashes of all of the above |

## Why a points gate

- **The gap in r2:** its primary metrics are start and 60+ minutes log loss. Better minutes
  probabilities can still leave expected points (xP) flat or worse, and r2 would not show it.
- **Expected-points error is a gate.** It keeps a minutes gain from being presented as a points
  gain without evidence.
- **Team points are reported only.**
  - A team's weekly score varies by about 15–20 points.
  - A plausible xP gain is well under 1 point a week.
  - Over 3–10 gameweeks that is noise.

## r3 contents (keys changed from r2)

1. **`points_metrics`**
   - **Unit:** player × eligible gameweek. The sim's xP is compared with official
     `total_points`, the `backtest.projection_backtest` unit.
   - **Metric:** primary is RMSE (root-mean-square error); MAE and bias are reported.
   - **Pairing:** exact, as Wegz checked: the same rows, cutoffs, refit schedule and seeds.
   - **Cohorts:**
     - all rows;
     - decision-relevant: FPL price ≥ 5.0 at the decision snapshot (F1);
     - secondary: last-5 points per fixture ≥ 3.0;
     - positions;
     - the r2 cohorts.
   - **Uncertainty:**
     - a player-cluster bootstrap (F2);
     - the Monte Carlo (simulation sampling) error rule (F3).
   - **Non-inferiority gate:**
     - ratio ≤ 1.005 on all rows;
     - ≤ 1.01 on the price cohort;
     - ≤ 1.01 on each position with at least 300 rows.
     - The gate uses point estimates and is labelled "no harm detected".
   - **Improvement-claim bar:** stricter than the gate.
2. **`team_points_report`:** a fixed-policy `replay_season(validated=True)`, reported only.
3. **`claims_rule`:** what each outcome allows anyone to say (F5 wording).
4. **`acceptance_rule`:**
   - amended with the points gate;
   - leave-one-evaluated-gameweek-out needs the same sign in at least two thirds of folds,
     rounded up (F5).
5. **`training_data_rule`:** F4, option (b), below. **Needs god's sign-off.**
6. **`sample_minimum_for_any_performance_claim`:**
   - `eligible_gameweeks` is now 5, with `evaluated_gameweeks` 3;
   - `player_gw_rows_for_points` is 1500.
7. **`earliest_possible_run`:** about GW13 if GW9 is the first eligible gameweek.
8. **`revision`.**

## Answers to Wegz's findings

- **F1, biased cohort: fixed.**
  - The decision-relevant cohort is now FPL price (`now_cost`) ≥ 5.0 in the decision snapshot.
    This is a cutoff-time quantity that neither arm produces.
  - On the 2026 GW8 snapshot that is 323 of 668 players, so about 970 rows over 3 gameweeks.
  - A price of 6.0 would give only 79 players a week, too few.
  - Last-5 points per fixture ≥ 3.0 is reported as a secondary cohort only.
- **F2, bootstrap unit: fixed.** Players are resampled as clusters with all their evaluated
  gameweeks: 2000 draws, seed 20261030.
- **F3, training regime and Monte Carlo error: fixed.**
  - **Training hook:** the implementation note now requires a new, reviewed training hook that
    applies `training_data_rule` to both arms in the same way. Without it, the points result is
    exploratory and cannot gate promotion.
  - **Monte Carlo error:**
    - Each arm runs with seeds 20261030 and 20261031, and the gate uses the mean of the two.
    - The Monte Carlo standard error of the ratio is |ratio₁ − ratio₂| / 2. It must be ≤ 0.001,
      a fifth of the margin.
    - If it is not, the sample count n is doubled from 800 and both seeds are run again.
    - The rule is fixed now.
- **F4, the validated training rule in r2 cannot be met: option (b) proposed. god must sign
  it off.**
  - **Shared minutes features:** both arms train on the full history visible at each cutoff.
    This means the baseline is production `sim-v1`, which is the comparison a promotion decision
    needs.
  - **Workload inputs:**
    - The candidate gets `obs_` values only on training rows from eligible gameweeks with repair
      proof, and NULL everywhere else.
    - `rec_` values are never used on the validated route.
  - **Added condition:** an evaluated gameweek counts only if at least 2 such eligible
    gameweeks come before it. Otherwise the candidate has no observed workload rows to learn
    from, and the comparison would be empty.
  - **Result:** 5 eligible gameweeks are needed, 2 for training and then 3 evaluated.
  - **Named skew risk:**
    - In training, workload is NULL on most rows (older history) and present only on recent
      rows. At evaluation it is present for every trusted linked player.
    - So whether workload is present is confounded with how recent the row is, and the model
      can learn time effects through the NULL pattern.
  - **Disclosure:**
    - the count of non-NULL workload training rows for each evaluated gameweek;
    - the paired result for trusted linked rows (workload present) and for unlinked or
      link-suspect rows separately. In those rows workload is NULL at evaluation, so any change
      can only come from this skew.
  - **Why not (a):**
    - (a) trains both arms on eligible gameweeks only, about 600–1,800 rows.
    - The baseline would then not be production `sim-v1`.
    - A gain over a starved baseline says little about whether to promote over the real one.
    - (a) also needs 4 or more eligible gameweeks, so it is barely earlier.
- **F5, wording: fixed.**
  - "...with no points harm detected (point estimate within margin)".
  - Leave-one-out needs the same sign in at least two thirds of folds, rounded up:
    2 of 3, 3 of 4, 4 of 5 or 4 of 6.

## Unchanged from r2

- the candidate and the baseline definition;
- the evaluation rows;
- the minutes metrics and their 2% bar;
- the r2 cohorts and the 1% limits for the NULL cohorts;
- the known limits;
- the exploratory route.
