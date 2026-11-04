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

PR #60 remains unmerged; master, live execution, `AGENT_MODE`, `plan.yml` and
deployment settings were not changed during this validation.
