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

## Testing layer

CI (`ci.yml`) blocks on: lint, format, the full test suite, a **coverage gate (85%)**, and a CLI smoke check. Run it all locally with `uv run pytest --cov`.

| Level | What it proves | Where |
|---|---|---|
| Unit | rules (scoring, bonus, auto-subs, prices), id matching, calendar, store | `tests/test_rules.py`, `test_data.py`, `test_understat_idmap.py` |
| Acceptance | the rules engine reproduces FPL's official points and bonus on recorded real gameweeks | `tests/test_rules.py` |
| Property-based | invariants for any input (bonus never mis-ranks, auto-sub always gives a legal XI) | `tests/test_properties.py` |
| Recorded real responses | every source's parser/contract passes on real captured payloads | `tests/fixtures/`, `test_contracts.py` |
| Format-change detection | each renamed/removed field, HTML instead of JSON, empty body is caught before reaching the database | `test_contracts.py`, `test_pipeline.py` |
| Failure handling | source down, 5xx, changed format: skipped and alerted, critical source aborts with nothing written, partial writes roll back, bad data is never exported | `test_pipeline.py` |
| Data quality | duplicates, orphan ids, impossible values, source disagreement, stored points vs the rules engine; every check is proven to fire on corrupted data | `db/quality.py`, `test_quality.py` |
| CLI smoke | the real commands run end to end on recorded data and return the right exit codes | `test_cli.py` |
| Live canary | the real sources still match the contracts and a full update succeeds (daily, plus manual) | `.github/workflows/canary.yml`, `tests/live/` |

`fpl-agent db-update` exit codes: `0` ok (non-critical sources may have been skipped and alerted), `2` a critical source (FPL) failed and nothing was written, `3` data failed quality checks and was not exported.

**Not covered yet:** models, solver and execution (they do not exist yet; each will ship with its own tests and a backtest gate), FotMob (next), authenticated FPL writes (the agent stays in dry mode), and the GitHub Actions scheduling itself. Branch protection must be switched on in the repo settings (require the `test` check) for a red CI to actually block merging; code cannot do that.
