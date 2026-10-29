# Command and automation reference

All commands are `uv run fpl-agent <command>`; `uv run fpl-agent <command> --help` lists every option.
The database is a DuckDB file (default `data_cache/football.duckdb`, git-ignored). In GitHub Actions
it is kept as a release asset.

## Commands

| Part | Command | Notes |
|---|---|---|
| Mode and settings | `status` | Says whether the agent is in dry or live mode and which optional settings are missing. Reads no credentials. |
| Public FPL snapshot | `snapshot` | Stores bootstrap + fixtures under `data_cache/snapshots`. |
| Build / update the database | `db-update --points [--reload-gws 5,6] --players --fotmob all --football-data E0,E1,E2 --understat-leagues EPL,La_liga,Serie_A,Bundesliga,Ligue_1` | FPL, FotMob (15 competitions), Understat, football-data.co.uk. Each source is written in its own transaction: a failing source's rows roll back, sources that succeeded stay committed (there is no whole-update rollback). Every run records each source's outcome in `source_attempts`, whatever its exit code. Quality checks run last, after the successful sources have committed, and nothing is exported unless they pass. Exit code 2 = FPL bootstrap, fixtures or player load failed (no FPL data from this run is loaded; a bootstrap or fixtures failure stops before any source data is written), 3 = data failed checks (already-committed sources stay, nothing exported). Automatic repair/quarantine deletes are **opt-in** (`--repairs`, also on `backfill`): without it they are only tried on a copy, and if one is needed the run stops with exit 3 (`*_blocked`, with the rows it would change) without applying it; a repair blocked before loading stops before any source data is written, a quarantine blocked at the end leaves the committed sources in place but unexported. Applied repairs are journalled; `repair-undo` lists them and reverses one exactly. `--no-repairs` is now the default and still accepted. |
| Database checks (read-only) | `db-schema`, `source-health`, `db-eligibility --season 2026` | `db-schema`: does the file match the declared schema (exit 3 = an open would add tables; a changed existing table is never altered and the code refuses to open it). `source-health`: as of now, each source's last finished attempt/success/failure and state (`failed`, `stale`, `ok_zero` = succeeded with no rows, `ok`, `never` = no record, reported as unknown) plus snapshot age and missing points; exit 0/1/2 = healthy/degraded/blocked; plans repeat the findings as notes, and a critical FPL source that failed after the plan's snapshot is a rail (`critical_source_failed`): a dry plan is then a diagnostic (exit 3), not a recommendation. `db-eligibility`: which past gameweeks are genuine decision-time evidence (`eligible`: official deadline recorded in time and one observed pre-deadline snapshot holding ≥95% of the players who scored), rebuilt (`reconstructed`) or missing (`unavailable`), and matches excluded for unresolved source conflicts. `backtest`/`replay --validated` enforce both (refuse non-eligible gameweeks, drop excluded matches from model inputs) and fit, pool and price only on what was observed by each deadline; without it their output is labelled exploratory. |
| FotMob identity links | `db-links [--season 2026]`, `db-links --propose --cutoff <ISO>`, `db-links --db <file> --apply [--reviewed proposals.json]`, `--undo <batch>`, `--batches` | Read-only by default: how many of the season's FPL players and minutes have FotMob detail joined, and why each unlinked player is unlinked. `--propose` (read-only) lists links a conservative appearance rule would make: same canonical fixture and side, minutes within 1, same start, goals, cards and saves, at least 2 agreeing matches covering 80% of both records, no contradiction, no rival record in either direction, and a FotMob-only record; each pair gets `valid_from`, the first date its evidence supported it. `--apply` is never automatic, needs an explicit `--db`, writes one batch in one transaction, and a retry adds nothing; `--undo` rolls a batch back exactly. Source rows keep their ids: a decision credits a linked record to the FPL player only if the link was written (`recorded_at`) and its stored evidence valid (`valid_from`) by then, and only while the evidence stored by then still supports it (otherwise it is listed as unsupported). Evidence counts only once stored (`ingested_at`); reconstructed snapshots never count. Conflict checks (`db-eligibility`) include linked records. Undo restores the data views; rolled-back rows stay as the record. |
| Backfill history to 2017/18 | `backfill --start 2017` | Resumable; includes the vaastav FPL archive (official points since 2016/17). Re-run until it reports nothing left. |
| Back-test the projections | `backtest --season 2025 --from-gw 10 --to-gw 30` | Point-in-time: models see only data before each deadline. Compares with "last 5 games" and FPL's own xP, per position. |
| Replay a past season | `replay --season 2025` | Runs the real planner, chips and rails through every gameweek and scores the decisions on the official points (auto-subs, vice cover, hits, Free Hit revert). The scoreboard for tuning the planner. |
| **Weekly plan (dry)** | `plan --entry <your team id>` | Reads your public squad, projects, plans, applies rails, prints the report. `--force` ignores the deadline-window gate; `--no-chips` disables chip search; `--out dir` writes `gw<N>.md/.json`; `--notify` posts to `NOTIFY_WEBHOOK`. |
| Review a finished gameweek | `review --gw 0` | `0` = latest gameweek with points. Scores predictions (by position and scoring component) and the recorded decision (captain regret, transfer gain after hits). |
| Lessons from a finished gameweek | `lessons --gw 0 --entry <id>` | Finds the week's mistakes on your real team (hand transfers, held moves that paid, captain, bench, goalkeeper, hits) and stores the lessons the next plan reads. |
| **Local dashboard** | `dashboard --port 8765` | Opens on http://127.0.0.1:8765 only. Overview, team, recommendations, players, planning, activity, data health, and three dry-only actions (refresh, dry plan, review). See [dashboard.md](dashboard.md). |

`plan` needs a database that has been through `db-update` for the upcoming gameweek. Your squad is
read from FPL's public endpoints (no login): selling prices are rebuilt from your transfer history,
free transfers from your gameweek history, and chips left from your chip history.

`fpl-agent --help` lists the remaining commands (`news-check`, `understat`, `gw-summary`, `explain`,
`send`, `coverage-check`, `fit-translations`, `verify-export`, `token-check` and others).

## Automation (GitHub Actions)

| Workflow | When | What |
|---|---|---|
| `ci.yml` | every pull request, pushes to `master` | ruff, format check, full tests, **85% coverage gate**, CLI smoke; Discord bot tests |
| `playwright.yml` | every pull request, pushes to `master` | dashboard browser tests |
| `data.yml` | daily + Tuesday | update the database from all sources and publish it |
| `backfill.yml` | manual / nightly until done | fill history from 2017/18 |
| `plan.yml` | every 2h | dry-run plan; acts only when a deadline is 45 min to 26h away (each run re-plans; the last one lands 45 min to 3h before it); transfers wait for team news until 5h before the deadline unless a planned player is about to change price that night; notifies once per gameweek; dry unless the `AGENT_MODE` variable is `live` |
| `token-check.yml` | every 3h | live mode only: signs in to FPL to keep the rotating token alive and alerts if it is refused; skipped in dry mode |
| `backtest.yml` | manual button | back-test a past season against simple baselines, or (mode `replay`) replay the whole season with the real planner; results in the run summary (read-only) |
| `review.yml` | Tuesday | score the finished gameweek, store its lessons for the next plan, publish the reviews and send the lessons and a gameweek summary to Discord (points, rank movement, captain, transfer gain, bench) |
| `canary.yml` | daily | check every live source still matches its contract |
| `discord.yml` | started by the Discord bot | `/explain` against the database (read-only), answered in Discord |
| `discord-deploy.yml` | manual button | put the Discord bot online and register its commands ([discord-bot.md](discord-bot.md)) |

## Testing

CI blocks on lint, format, the full suite and an 85% coverage gate. Every source has recorded real
responses and a contract; the simulator is tested against a synthetic league with known truth; the
planner is checked against brute force on a small problem; execution and auth are tested only against
mock transports. The data archive scripts (`scripts/data_*.sh`) need Linux with `zstd`; their tests
are skipped elsewhere and run in CI.

| Level | What it proves |
|---|---|
| Rules acceptance | the rules engine reproduces FPL's official points and bonus on recorded real gameweeks |
| Property tests | bonus never mis-ranks, auto-sub always yields a legal XI |
| Source contracts | each renamed/removed field, HTML-instead-of-JSON, empty body is caught before reaching the database |
| Failure handling | a source down is skipped and alerted, its rows rolled back; a critical source aborts the run (its data rolled back, the attempt still recorded); bad data is never exported |
| Data quality | duplicates, orphans, impossible values, cross-source disagreement, stored points vs the rules engine |
| Model tests | recovery of known strengths, calibrated minutes, double gameweeks, odds blending, determinism |
| Planner | legal squads, budget, banking, hits, chips, Free Hit, equals brute force |
| Rails and execution | every rail fires; dry mode makes zero requests; preview must match before confirm |
| End to end | gate -> squad -> projection -> decision -> rails -> report on a synthetic league |
| Live canary | daily check of the real sources |

## Recorded back-test (past measurement, not a guarantee)

The 2025/26 backtest (GW10 to 30, 16,522 player-weeks, run 10 Oct 2026) beat both baselines in every
position: RMSE 1.91 against 2.11 for "last 5 games" and 2.55 for FPL's xP, correlation 0.59 against
0.50 and 0.19. Season replays (two runs per setting, both seasons) put Bench Boost and Triple Captain
thresholds of 14 and 8 expected points ahead of the old 8 and 4: 2024/25 1,969 against 1,963 on
average, 2025/26 2,166 against 2,108. The season replay has no ownership history, so it runs without
the ownership rails and the rank-risk term. Past back-tests do not predict future results.
