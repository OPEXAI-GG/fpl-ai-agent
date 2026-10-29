# Draft release note: model integrity repairs

Status: review draft only. The proposed patch is not merged or deployed.

The changes proposed for PR #55:

- Limit simulated starting outfielders to ten and substitutes to five. Weighted selection now keeps near-certain starters in the lineup; the branch's existing one-goalkeeper rule is retained.
- Compare backtests on common player-game rows only. `beats_baselines` now requires per-row inputs and at least 20 shared rows. The `fpl backtest` output includes the paired comparison and dropped-row counts.
- In prediction review, choose the newest run saved before the deadline and count missing outcomes instead of assigning them zero points. Calibration uses only reviews marked eligible.
- Score card-only point rows. In replay, a yellow or red card at zero minutes counts as participation for automatic substitutions, while captain handover still depends on minutes played. This matches the [2026/27 FPL rules](https://www.premierleague.com/ar/news/4661029). Live team planning is unchanged by this replay-only card handling.
- Correctly label whether a prediction review used an official deadline or the first-kickoff-minus-90-minutes fallback.

The simulator changes alter live projections. Stored reviews without the eligibility mark no longer affect calibration, so the factors return to 1.0 until eligible reviews are recorded. The branch's existing pre-deadline odds cutoff, one-goalkeeper handling, and break-even transfer-lesson rule are retained; they are not new changes in this patch.

Validation is structural, not predictive. Wegz ran 86 model tests and passed code review on r3 commit `4c107b7`. Read full-suite and Linux continuous-integration status from the latest PR #55 head; checks on that branch do not establish model accuracy. There are 0 eligible 2026 gameweeks, so no accuracy improvement or performance lift is established.

Known limits remain: substitute minutes are not tied to starters leaving; missing-goalkeeper degradation is not reported; the simulated chip path does not pass cards; and deadline provenance still needs its separately reviewed operational path. Do not describe this as a validated model or promise highest points.
