# Proposal r3: points metrics for the `minutes-workload-v0` comparison plan

Owner: Nabil, 2026-10-30. Status: PROPOSED, not applied. Needs Wegz's independent review.
No comparison has run. Nothing here was run or tuned on any gameweek.

## Answer

**Yes, add both, but with different roles.**
- **Paired expected-points (xP) error: required gate.** It keeps a minutes gain from being
  presented as a points gain without evidence.
- **Fixed-policy team points: reported only.** It is never a gate and never a claim.

Why the r2 plan is not enough: its primary metrics are start and 60+ minutes log loss. Better
minutes probabilities can still leave xP flat or worse. For example, the sim may already use
the minutes signal well, or the change may move only low-xP rotation players nobody picks.
r2 has no metric that would show this.

Why team points cannot be a gate:
- A single team's weekly score varies by about 15–20 points, mostly from goals and captain
  luck.
- A plausible xP gain moves expected team points by well under 1 point a week.
- Over 3–10 eligible gameweeks that is far below the noise, so a team-points gate would pass or
  fail at random. It is useful as a sanity check, not as evidence.

## What changes in `comparison-plan.json` (r2 → r3)

The r2 file (`artifacts/model-factor-workload-20261030/comparison-plan.json`, sha256 48cb7e26…)
is in draft PR #56. **It is not edited here.** The full r3 text is in
`comparison-plan-r3.proposed.json` and the exact difference is in `r2-to-r3.diff`. Changed keys:

1. **`points_metrics` (new).** Paired xP error, the unit, the rows, the thresholds and the
   implementation. The implementation reuses `model/backtest.py`'s `paired_comparison` logic,
   with the candidate in place of a baseline.
2. **`team_points_report` (new).** Fixed-policy replay, reported only.
3. **`acceptance_rule` (amended).** The points non-inferiority gate is added to promotion.
4. **`claims_rule` (new).** What each result allows anyone to say.
5. **`sample_minimum_for_any_performance_claim` (amended).** Adds `player_gw_rows_for_points`:
   1500.
6. **`revision` (amended).** Says r3 and why.

Not changed: the candidate, baseline, training rule, evaluation rows, cohorts, minutes metrics
and their 2% bar, and the known limits.

## The proposed metric and thresholds

- **Unit.** One row per (player, eligible gameweek). The predicted value is that gameweek's
  expected FPL points from the sim, which sums double-gameweek fixtures. The actual value is
  official `total_points` from `fpl_points_gw`, summed over the gameweek's fixtures. This is
  the unit `model/backtest.py` already uses.
- **Pairing.**
  - Baseline `sim-v1` and candidate `sim-v1-workload` are scored on exactly the same rows,
    cutoff, seed and sample count.
  - Rows where either side is missing are counted and reported, not silently dropped, as
    `paired_comparison` does now.
- **Metric.** The primary is RMSE of xP against actual points, the same metric as
  `beats_baselines`. MAE and bias are reported too.
- **Decision-relevant cohort.** Rows where the baseline's xP is at least 2.0. The cohort is
  defined by the baseline only, so the candidate cannot choose its own rows. These are roughly
  the players a manager would consider; most rows are bench and rotation players near 0.
- **Uncertainty.**
  - A paired bootstrap of the RMSE difference, resampling players within each gameweek
    (2000 draws, seed 20261030).
  - The 95% interval is reported with every number.
- **Gate (needed for promotion, together with the r2 rule): non-inferiority.**
  1. All rows: candidate xP RMSE ≤ baseline × 1.005.
  2. Decision-relevant cohort: candidate xP RMSE ≤ baseline × 1.01.
  3. Each position cohort with at least 300 rows: candidate ≤ baseline × 1.01.
- **Bar for saying "improves points" (a stricter, separate claim):**
  1. All rows: a paired RMSE reduction of at least 1%.
  2. The upper bound of the bootstrap 95% interval of the difference is below 0.
  3. The decision-relevant cohort does not regress.
  4. The same sign in at least 2 of 3 or more gameweeks, leaving one gameweek out at a time.
- **No tuning.** The thresholds are fixed now. They cannot be changed after any eligible
  gameweek is evaluated without a new revision and a new set of gameweeks.

## Team points (reported only)

- **Policy.** `model/replay.replay_season(validated=True)` is run twice, with one model and
  then the other.
- **Fixed settings.**
  - The same `ReplayConfig` defaults: horizon, samples, seed and solver settings unchanged.
  - The same starting squad: the real squad at the first evaluated deadline when known, else
    the same free first pick for both runs.
  - The same chip rules and the same eligible gameweeks.
- **Reported.**
  - Actual points per gameweek and in total, for each run and the difference.
  - Captain points.
  - The number of gameweeks where the decisions differ at all.
- **Label.** Always "descriptive; not evidence of a points gain". It never enters
  `acceptance_rule`.

## The claims rule (what may be said)

- **Minutes metrics pass, xP gate fails:** "improves minutes forecasts; no points benefit
  shown". It must not be promoted.
- **Minutes metrics pass, xP non-inferior:** "improves minutes forecasts without hurting
  points". Promotion is allowed if every other r2 condition holds.
- **Points improvement bar passes:** only then may "improves expected-points accuracy" be said.
- **Team points:** never "scored more points" from replay results, whatever the sign.
- **Exploratory or reconstructed gameweeks:** never any claim.

## Coordination

- **PR #56 (Sami):** the package with the r2 plan and its manifest is in Sami's in-flight PR.
  This proposal does not touch it. The choice is up to Sami and god:
  - after Wegz passes, bring r3 into PR #56;
  - or keep it as a follow-up.
- **The run itself waits for the same preconditions as r2:** FAA-41 live, plus 3 eligible
  gameweeks with repair proof.
