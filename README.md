# fpl-ai-agent

Autonomous Fantasy Premier League agent: statistical points model, a news "scout", a learning loop,
a MILP solver and (later) automatic execution. Build plan lives in the project files; this repo is
built phase by phase through small PRs.

**Status (plan v2):** the football database (DuckDB; `fpl-agent db-update`) is the core every model will read from. Writers so far: FPL (players, fixtures, official points with explain), Understat (match xG, per-player match stats, shots), football-data.co.uk (PL + Championship results, xG, odds). FotMob, backfill from 2017/18 and the weekly workflow come next.

**Earlier status:** Phase 0 (scaffold) and Phase 1 start (public FPL client, snapshot store, deadline calendar).

## Safety defaults

- `AGENT_MODE` defaults to `dry`. Nothing touches a real FPL account until you set `live`.
- `AGENT_ENABLED=false` is a kill switch that makes every run a no-op.
- No secrets in the repo. Credentials come from GitHub repo secrets / environment variables only.

## Development

```bash
uv sync
uv run ruff check . && uv run ruff format --check .
uv run pytest -q
uv run fpl-agent snapshot      # pull public FPL data into data_cache/snapshots (git-ignored)
uv run fpl-agent db-update --points --players --limit 25   # build data_cache/football.duckdb
```

Tests run against recorded fixtures in `tests/fixtures/`, so they need no network.

## Secrets and variables (set when the phase that needs them lands)

| Name | Kind | Needed from | Purpose |
|---|---|---|---|
| `ANTHROPIC_API_KEY` | secret | scout (M2B) | Claude API for the scout |
| `ODDS_API_KEY` | secret | data pipeline (M1) | The Odds API (free tier to start) |
| `NOTIFY_WEBHOOK` | secret | dry report (M3) | Telegram/Discord webhook |
| `FPL_ENTRY_ID` | variable | M3 | your team id |
| `FPL_REFRESH_TOKEN` | secret | execution (M5) | one-time paste from browser |
| `GH_SECRETS_PAT` | secret | execution (M5) | fine-grained PAT, Secrets read/write, to rotate the token |
| `AGENT_MODE` | variable | M3 | `dry` (default) or `live` |
| `AGENT_ENABLED` | variable | M3 | kill switch |
