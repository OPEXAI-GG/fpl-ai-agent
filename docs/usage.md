# Usage

Run commands from the repository root. Use `uv run --frozen fpl-agent <command> --help`
for the current options. Installation is covered in the [quick start](../README.md#quick-start).

## Your first team plan

### 1. Prepare the database

The default database is the ignored file `data_cache/football.duckdb`. A useful initial update is:

```bash
uv run --frozen fpl-agent db-update --points --players --fotmob ENG1
```

This reads public football endpoints and can take time on an empty database. Optional feeds
can fail independently; inspect the source and quality summary before proceeding. Exit `2`
means the critical FPL source failed; exit `3` means the quality gate failed.

To restore an existing published export, the repository provides
`scripts/data_restore.sh data_cache/export`. It requires GitHub CLI access and a `data` release.
Pass `--import-dir data_cache/export` on the first command that opens a fresh local database.
The restore script can fall back to an empty directory; verify that tables were actually restored.

### 2. Identify your team

Your public entry ID is the number in an FPL link such as
`fantasy.premierleague.com/entry/123456/event/9`. It is not a login credential.
Keep personal configuration in your environment or environment settings, outside Git.

### 3. Request recommendations

Replace the example entry ID before running:

```bash
FPL_ENTRY_ID=123456 AGENT_MODE=dry uv run --frozen fpl-agent plan \
  --force --out data_cache/plans
```

`--force` permits an early planning run outside the normal deadline window. It does not
enable live execution. Omit it for the scheduled deadline-window behavior.

The report covers transfers, chips, the XI, captaincy, and relevant constraints. The default
planning horizon is eight gameweeks; use `--horizon` to change it. Output files are
`data_cache/plans/gw<N>.md` and `.json`. Refresh the data and rerun near the deadline,
especially after price, availability, or fixture changes.

Public endpoints describe the last deadline's squad. The planner detects transfers made
since then; inspect the report if you have already changed your team. Authenticated current-team
reads belong to the optional live path described in [operations](operations.md).

## Command reference

All entries below follow `uv run --frozen fpl-agent`.

| Command | Purpose |
| --- | --- |
| `status` | Show the execution mode and which optional settings are present |
| `snapshot` | Save public bootstrap and fixture snapshots |
| `db-update --points --players --fotmob all --football-data E0,E1,E2 --understat-leagues EPL,La_liga,Serie_A,Bundesliga,Ligue_1` | Update all documented source families and check data quality |
| `backfill --start 2017` | Resume historical ingestion from 2017/18 |
| `plan --entry 123456 --force --out data_cache/plans` | Generate a personalized plan; replace the example ID and keep `AGENT_MODE=dry` |
| `review --gw 0` | Score the latest gameweek with stored points and report finality |
| `lessons --gw 0 --entry 123456` | Review the manager's decisions and retain lessons from final outcomes |
| `features` | Show effective feature modes and their source |
| `evaluate --gw 0` | Score eligible raw/issued forecasts and valid matched experiments |
| `learn --gw 0` | Evaluate a bounded update from finalized evidence |
| `learn --history` | Inspect versioned learning state |
| `learn --rollback` | Restore the preceding learning configuration |
| `backtest --season 2025 --from-gw 10 --to-gw 30` | Compare point forecasts with historical baselines |
| `replay --season 2025` | Replay planning and chips against historical results |
| `walk-forward --season 2025 --from-gw 6 --to-gw 38` | Validate workload features with a chronological tune/test split |

Use `--db`, `--season`, and `--import-dir` where supported to work on a separate dataset.
Most current-season commands default to season `2026`; historical evaluation commands default
to `2025`. Confirm command help when working across seasons.

## Interpreting feature modes

`FEATURE_WORKLOAD=shadow` is the default: the planner uses its baseline while storing a
matched workload experiment. `off` disables the overlay; `on` applies it to the plan.
Environment overrides take precedence over learning state. The historical workload evaluation
has not established an accuracy benefit, so retaining shadow mode is the documented default.

For promotion thresholds, cutoffs, missing results, and rollback, read
[evaluation and learning](intelligence.md). For notifications and Actions, read
[operations](operations.md).
