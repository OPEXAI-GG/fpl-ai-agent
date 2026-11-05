# Independent runtime validation — 4 November 2026

Model code tested: `e510c138567386252715f04c048331750402610d`.

## Dataset and execution

Downloaded the `data` release's `football-db.tar.zst` and verified SHA-256:
`d3cc931b3e133b9f89eb2ad6332af26b64b8ca7ea1b5e7970a371eaec46f9814`.
Imported into isolated local DuckDB copies. No release assets, production database,
team settings or deployment configuration were updated. Model commands used two
OpenMP/OpenBLAS/MKL threads. These are local runtime tests; GitHub Actions dispatch
was blocked by the cloud environment's outbound network policy.

## Current forecast and workload experiment

Ran `agent.forecast` and `agent.run_experiments` for 2026/27 GW10–12 at
2026-11-04 12:18:04 UTC, using the released snapshot from 3 November.
Registered the runs as local validation, with 1,000 samples per arm.

- GW10 player pool: 670; workload adjustments recorded for 670 players.
- Feature mode: `workload=shadow`; no authenticated FPL action or notification.
- The control reproduced the primary GW10 expectations exactly: maximum difference 0.
- Mean absolute expected-points difference with workload: 0.04853; maximum 0.449.
- Workload model training rows: 235,454.

These checks demonstrate that the feature changes forecast probabilities and points.
They do not establish improved accuracy.

## Historical workload walk-forward

Executed `fpl-agent walk-forward --season 2025 --from-gw 6 --to-gw 38 --refit-every 4`
against the verified database copy. Tuning: GW6–21; test: GW22–38, 13,935
player-fixtures, workload coverage 100%.

| Predictor | Selected bound | Test Brier | Test log loss |
| --- | --- | --- | --- |
| Baseline | — | 0.07523 | 0.24249 |
| All workload features | 0.05 | 0.07527 | 0.24274 |
| Rest only | 0.05 | 0.07526 | 0.24251 |
| Without club congestion | 0.05 | 0.07528 | 0.24278 |

The all-workload arm improved Brier in 8 of 17 test weeks. The small differences from
the earlier documented run do not change the conclusion: no accuracy gain is
established. Workload remains in shadow. The runtime overlay's configured bound is
0.10; this experiment's tuned bound was not deployed.

## Workflow commands and remaining limits

- The projection-backtest command completed for 2025/26 GW38: 841 player rows,
  simulator RMSE 1.806 versus last-five 1.991 and FPL xP 1.667. This single gameweek
  is a functional check, not a season-level accuracy claim.
- `review --gw 9`, `evaluate --gw 9` and `learn --gw 9` executed on the release copy.
  Evaluation/learning correctly stopped at `not_final`: the older release has no
  recorded FPL event finality. Live refresh was blocked by network access to
  `fantasy.premierleague.com`. No learning update was adopted from these commands.
- Remote Actions dispatch was blocked at `api.github.com`. The cloud network draft
  now requests those two destinations; it must be applied before remote dispatch
  and live finality refresh can be verified. No additional credential was requested.
- The completed code validation remains: 542 passed, 1 skipped, 13 deselected,
  92.98% coverage; lint/format, CLI smoke and all 12 Discord tests passed.

## Follow-up verification with GitHub and FPL access (4 November 2026, 13:29–13:45 UTC)

Branch head verified: `7a42e5348b2372e409d351e922786aa6651c168b` (contains `e510c13`).
No application code changed in this follow-up; only this report.

### GitHub Actions

| Run | Revision | Conclusion | Observed |
| --- | --- | --- | --- |
| CI [41829390060](https://github.com/OPEXAI-GG/fpl-ai-agent/actions/runs/41829390060) | `7a42e53` | success | test with coverage gate, ruff, CLI smoke, discord-bot (on the exact head; reused) |
| backtest.yml [41838576734](https://github.com/OPEXAI-GG/fpl-ai-agent/actions/runs/41838576734) | `7a42e53` | success | `workflow_dispatch`, mode `projections`, season 2025, GW38–38 |

The backtest artifact (`backtest`, id 13327862743, zip sha256
`171efe9ff383b29d4f7bc6fa10c920a9288a5e15ef496d5966f24d3bdd7b3424`) holds
`backtest.csv` with **841 prediction rows** for GW38, every row with a simulator
prediction (`sim`), matching the local run above. The workflow is read-only
(`contents: read`, no publish step).

### Weekly review, evaluation and learning on an isolated copy

Database: the same `data` release asset (sha256 `d3cc931b…9814`, unchanged since
3 November), imported into a scratch DuckDB file. The release and the production
database were not touched; `--export` wrote to a scratch directory that was never
published, and `NOTIFY_WEBHOOK` was unset.

1. `db-update --football-data E0 --points` (the review workflow's first step) refreshed
   genuine public FPL event status at 13:32 UTC: GW6–9 `finished` and `data_checked`
   true, GW9 deadline 2026-10-31 11:00 UTC; GW10 not finished (deadline 2026-11-06
   18:30). GW9 points: 670 rows.
2. `review --gw 0` selected **GW9**, `final: true` ("finished and data checked"),
   `eligible: true`. Scored run `0f1f919251de`, created 2026-10-23 13:21 UTC, before the
   FPL deadline (deadline source `fpl`); it is a legacy run (saved before the run
   registry existed). n 668, MAE 1.107, RMSE 1.943, bias −0.043, missing actuals 0.
3. `evaluate --gw 0` recorded the raw metrics for that run (n 668, MAE 1.1067, RMSE
   1.9429, bias −0.0432) in `evaluations` and `reviews`. **No experiment arms exist** for
   this gameweek: the run predates them, so the workload comparison is reported as
   `valid: false, reason: legacy run` and there is no `issued` record. None were
   fabricated.
4. `learn --gw 0`: `retained`, no learning state written. Workload has 0 of 4 required
   gameweeks and 0 of 60 player-weeks of matched evidence; calibration stays the
   agent's review calibration (no state exists). One `learning_updates` row: status
   `retained`, evidence key `1e4fbad7…`.
5. Repeating `evaluate` and `learn` unchanged: `learning: unchanged … GW9 already
   processed with identical evidence (1e4fbad7): nothing changes`; still one
   `learning_updates` row and no `learning_state` rows.

### review.yml integration

`review.yml` runs `evaluate --gw 0` and `learn --gw 0` (both with `--export`) after the
review and before lessons and publishing, in a `continue-on-error` step. Those
commands were executed **locally** exactly as written (steps 1–5 above), excluding the
lessons/summary notifications and `scripts/data_publish.sh`. The review workflow itself
was **not** dispatched on GitHub Actions, because every run publishes the data release
and can send Discord messages.

PR #60 remains unmerged; master, live execution, `AGENT_MODE`, `plan.yml` and
deployment settings were not changed during this validation.
