# Configuration and operations

## Runtime configuration

Account credentials and API keys belong in environment settings or GitHub Actions secrets.
Personal team IDs and other non-secret settings belong in environment variables or Actions
variables. Do not commit local databases, tokens, webhook URLs, or personal configuration.

| Setting | Purpose | Default / requirement |
| --- | --- | --- |
| `FPL_ENTRY_ID` | Public manager entry ID | Required for a personalized plan |
| `AGENT_MODE` | `dry` or `live` execution | `dry` |
| `AGENT_ENABLED` | Agent kill switch | `true`; `false` makes a run a no-op |
| `FEATURE_WORKLOAD` | `off`, `shadow`, or `on` | `shadow`, unless valid learning state changes it |
| `ODDS_API_KEY` | Optional bookmaker inputs | Skipped when absent |
| `ANTHROPIC_API_KEY` | Optional bounded scout over FPL's own news text | Skipped when absent |
| `SCOUT_MODEL` | Scout model selection | Repository default |
| `NOTIFY_WEBHOOK` | Optional report and alert destination | Used by explicitly notifying commands/workflows |

The workflows also accept the existing `Discord_webhook` secret as a fallback notification
destination. Discord worker configuration is documented in the [Discord guide](discord-bot.md).

## Execution modes

In dry mode, the agent reads public squad information and reports its recommendation.
It makes no authenticated FPL request. Optional odds and scout services can still use their
own configured keys.

Live execution is implemented but has been tested only against mock servers. It uses a
rotating refresh token, transfer preview/confirmation, lineup submission, and read-back checks.
Enabling it requires an explicit operational decision and these additional settings:

| Setting | Storage | Purpose |
| --- | --- | --- |
| `FPL_REFRESH_TOKEN` | Secret | FPL refresh token |
| `FPL_TOKEN_URL` | Variable | Sign-in endpoint from the manager's browser session |
| `FPL_CLIENT_ID` | Variable | Client ID from the same session |
| `GH_SECRETS_PAT` | Secret | Scoped permission to store the rotated token back in repository secrets |

The scheduled plan reads `AGENT_MODE` from the repository variable. Setting
`AGENT_ENABLED=false` stops the agent. Never include credential values in issue reports.

## GitHub Actions

| Workflow | Trigger | Behavior |
| --- | --- | --- |
| [CI](../.github/workflows/ci.yml) | PRs and master pushes | Lint, formatting, coverage gate, CLI smoke, and Discord tests |
| [Data](../.github/workflows/data.yml) | Daily / weekly and manual | Refresh and publish the football database |
| [Backfill](../.github/workflows/backfill.yml) | Scheduled / manual | Resume historical ingestion |
| [Plan](../.github/workflows/plan.yml) | Every two hours and manual | Deadline-gated planning; publishes reports/database and can notify |
| [Backtest](../.github/workflows/backtest.yml) | Manual | Read-only projection, replay, retro, or explanation workflow; uploads artifacts |
| [Review](../.github/workflows/review.yml) | Tuesday and manual | Review, evaluate, learn, collect lessons, publish, and notify |
| [Canary](../.github/workflows/canary.yml) | Daily and manual | Check live source contracts |
| [Discord reply](../.github/workflows/discord.yml) | Bot / manual | Read-only explanation with an explicit Discord reply |
| [Discord deployment](../.github/workflows/discord-deploy.yml) | Manual | Deploy the worker and register commands |

Check each workflow's declared inputs and permissions before dispatch. A successful local
command is distinct from a successful Actions run. The review workflow has publishing and
notification side effects; validate its stages on isolated data when testing.

The plan's normal action window is 45 minutes to 26 hours before the deadline. Transfers can
wait for team news until five hours before it, subject to price-pressure logic. A manual dry
workflow input keeps the run dry and suppresses publishing/notifications as implemented in
`plan.yml`; inspect its current inputs before use.

## Data lifecycle and reliability

- Local working data lives under ignored `data_cache/`; the database is DuckDB.
- Actions restore and publish parquet exports using the `data` release asset.
- Schema migrations are additive. Keep historical exports and use a copy for experiments.
- Optional source failures are reported; the quality gate controls export readiness.
- A forecast records its cutoff, version, configuration, and eligible comparison runs.
- Finality requires finished and checked FPL event status plus stored points. An older export
  without event status needs a successful data refresh before evaluation or learning can proceed.
- Learning retains the current state when evidence is missing or insufficient. Use
  `learn --history` and `learn --rollback` for inspection and recovery.

See [intelligence.md](intelligence.md) for the detailed evaluation contract and
[architecture.md](architecture.md) for the component map.
