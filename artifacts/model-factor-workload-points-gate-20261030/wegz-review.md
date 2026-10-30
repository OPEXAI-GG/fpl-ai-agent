# Review: comparison plan r3 proposal, points gate (Wegz, 2026-10-30)

Inputs, with hashes I checked:
- PROPOSAL.md 9956e29e
- comparison-plan-r3.proposed.json 9b17a9e1
- r2-to-r3.diff 6fd839db

Parsed as JSON against r2 (48cb7e26), the changed keys are exactly: points_metrics,
team_points_report, claims_rule, acceptance_rule, sample_minimum, revision. Nothing else
changed. Nothing was run.

**Verdict: CHANGES NEEDED.**
- The design is right: xP error is a gate, team points are reported only, and the claims rule
  is sound.
- Four points must be fixed before the plan is frozen. One of them (F4) is in r2, which I
  passed and missed.

## Answers to the three probes

**1. Thresholds: defensible before data, with one wording fix (F5).**
- The non-inferiority margins (1.005 on all rows, 1.01 on cohorts) are tight, fixed now, and
  protected by the no-tuning rule.
- The improvement bar (at least 1%, a 95% upper bound below 0, leave-one-gameweek-out) is
  appropriately stricter.
- The gate compares point estimates only; the bootstrap interval is not used in it. That is
  acceptable for a "do no harm" guard, but it does not show the absence of harm.

**2. A cohort defined by the baseline is NOT free of selection bias (F1).**
- Selecting rows on "baseline xP >= 2.0" conditions on the baseline's own prediction.
- Rows enter partly because the baseline over-predicted them (regression to the mean).
- That inflates the baseline's error inside the cohort and biases the comparison toward the
  candidate.
- Fix: define the cohort symmetrically or independently of both arms:
  - the mean of the two arms' xP >= 2.0;
  - or, preferably, a cutoff-time quantity neither arm produces, such as last-5 points per
    fixture, or FPL price at the cutoff snapshot.

**3. Reusing projection_backtest keeps the row pairing exact but not the r2 training rule
(F3).**
- Row pairing (checked at 41b0a6a):
  - the refit gameweeks depend only on `gw` and `history`, not on the model;
  - rows come from the same pool, the same `actual` table and the same `touched` exclusions;
  - so rows, cutoffs and the refit schedule pair exactly.
- But the function fits `MinutesModel.fit(history)` on all history visible at the cutoff,
  including reconstructed gameweeks and earlier seasons.
- It has no hook to:
  - limit training rows to eligible gameweeks (the r2 VALIDATED rule);
  - supply `obs_` workload values at each training row's own cutoff.
- As written, the points gate would therefore be computed on a different training regime from
  the minutes gate, which in substance is the exploratory route.

## Findings

- **F1 (must): biased cohort.** See probe 2. Use one of the cohort definitions given there.
- **F2 (must): bootstrap unit.**
  - Resampling players within each gameweek treats one player's gameweeks as independent.
  - That understates the variance, because a player's errors are correlated across gameweeks.
  - Resample players as clusters: draw players with replacement and keep all their gameweeks.
  - Keep 2000 draws and seed 20261030.
- **F3 (must): training regime.**
  - The implementation note must say that both arms use the r2 VALIDATED training rule, through
    a training-rows and feature hook applied identically to both.
  - Otherwise the points result must be labelled exploratory, and then it cannot gate
    promotion.
  - Also report the Monte Carlo standard error of xP. The same seed does not guarantee common
    random numbers once the minutes draws differ, so `SimConfig.n` must make simulation noise
    small next to the 0.5% margin.
- **F4 (must; an r2 defect I missed in my r2 PASS): the validated rule is not feasible as
  written.**
  - "Training rows come only from eligible gameweeks before the evaluated gameweek" means the
    first eligible gameweek has no training rows.
  - Three eligible gameweeks therefore give at most 2 evaluations, trained on 1-2 gameweeks
    (about 600-1,200 rows) of minutes data.
  - The baseline trained that way is not production `sim-v1`, which trains on full history.
  - So the 3-gameweek minimum is not reachable at about GW12.
  - A decision is needed: Nabil proposes and god signs off. Options:
    - (a) count eligible *evaluated* gameweeks (at least 3, so at least 4 eligible in total);
    - (b) let both arms train the shared minutes features on full visible history, with only
      the candidate's extra workload inputs restricted to `obs_` from eligible gameweeks (NULL
      elsewhere). This must be stated as a deliberate change to P1, with its skew risk named.
  - Until then, `earliest_possible_run` (about GW12) is optimistic.
- **F5 (minor): claims wording.**
  - "improves minutes forecasts without hurting points" should read "... with no points harm
    detected (point estimate within margin)".
  - Leave-one-gameweek-out "the same sign in at least 2 of 3 or more" should state the rule for
    larger counts, for example "in at least two thirds of folds".

Not blocking: the team-points report design, the 1500-row points minimum and the position
cohorts of at least 300 rows are fine.

Nothing is approved for running, wiring or promotion.

## Rev 2 re-review (comparison-plan-r3.json b6341677)

All hashes.sha256 entries match. I read r3-rev1-to-rev2.diff (1fd07581) in full. The file is
valid JSON.

**Verdict: PASS once one fix is made (U1). God must sign off on F4 option (b), a deliberate
change to r2 P1.**

- **F1 closed.** Price at the decision snapshot is known at the cutoff and is produced by
  neither arm. Last-5 is reported only.
- **F2 closed.** The bootstrap now resamples players as clusters.
- **F3 closed.**
  - A reviewed training hook is required; without it the result is exploratory and cannot gate.
  - The MC standard error from two seeds, |r1 - r2| / 2, is correct for the mean of two runs.
    It has one degree of freedom, which is accepted as declared.
- **F4 closed by option (b).**
  - The baseline is production sim-v1.
  - The candidate's workload inputs are obs_ only, from eligible repair-proof gameweeks before
    the evaluated one, and NULL elsewhere. rec_ is never used on the validated route.
  - An evaluated gameweek needs at least 2 such gameweeks before it, so 5 eligible and 3
    evaluated in total.
  - The NULL/recency skew is named, and it is disclosed through the per-gameweek non-NULL
    training count and a separate paired result for rows that are NULL at evaluation.
  - With refit_every=6, workload rows are taken only from before the fit gameweek, so there is
    no leak.
- **F5 closed.**

**U1 (must, small): the price unit.**
- `fpl_players_gw.now_cost` is stored in tenths of a million: the quality check bounds it at
  30-250, and ingest stores the raw API integer.
- Read literally, "now_cost >= 5.0" selects every player. The GW8 count of 323/668 implies
  `now_cost >= 50`.
- Write it as "now_cost >= 50 (GBP 5.0m)".

**U2 (minor):** `baseline` still says "as on master 51f0def". Say instead: the same code
revision as the candidate at run time, without the extra inputs. That way both arms share the
P0-repaired simulator.

Once U1 is fixed, send the new hash. That is the file to commit, and I will name its sha256.

## r4 review (comparison-plan-r4.json 42a584ad): PASS

- All 12 hashes.sha256 entries match, and the file uses LF line endings and is valid JSON.
- I read r3rev2-to-r4.diff (a011b352) in full.
- **U1 closed.** The decision cohort is `fpl_players_gw.now_cost >= 50` (GBP 5.0m, tenths) in
  the decision snapshot (snapshot_at <= C_g).
- **U2 closed.** The baseline is the same code revision as the candidate at run time, without
  the workload inputs.
- **God's option (b) conditions are all met:**
  - shared features train on the full history visible at C_g;
  - workload inputs are obs_ at C_t, only for eligible repair-proven t < g, and NULL elsewhere;
    rec_ is never used;
  - the missingness and recency risk is stated, with its disclosure;
  - there are at least 3 workload-training gameweeks (with at least 1,200 trusted non-NULL rows)
    and at least 3 evaluated gameweeks;
  - earliest_possible_run is moved to after GW14 if GW9 is the first eligible gameweek.
- **F3 regime is exact.** Both arms are refit at every evaluated gameweek (refit_every=1) on the
  same rows, with shared ratings, known-at odds, pool, SimConfig and exclusions, through one
  reviewed hook. MCSE is reported per ratio, with an all-rows tolerance of 0.001. LOGO uses
  ceil(2k/3) folds in both the claim bar and the acceptance rule.

Passed as the plan to commit: sha256
42a584ada7b80dc6a48455e2c5241ff2e27d49989643150e2daec5775052c2d2. This is a plan only: no
comparison, run, wiring or performance approval.
