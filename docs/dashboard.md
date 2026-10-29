# Local dashboard

Matchroom is the agent's dark football analytics dashboard, served from your own machine. It reads the agent's DuckDB
database and runs three safe actions. There is nothing new to install: it uses Python's
built-in web server and plain HTML, CSS and JavaScript, with no build step and no CDN.

## Start

```text
uv sync --frozen
uv run fpl-agent dashboard --db data_cache/football.duckdb --port 8765
# open http://127.0.0.1:8765   (Ctrl+C stops it)
```

Without uv, use the project's virtual environment instead:
`.venv/Scripts/python -m fpl_agent.cli dashboard` on Windows, `.venv/bin/python -m fpl_agent.cli dashboard` elsewhere.

Options:

| Option | Default | Meaning |
|---|---|---|
| `--db` | `data_cache/football.duckdb` | the database to show (it is never created or migrated by the page) |
| `--port` | `8765` | port on 127.0.0.1 |
| `--state-dir` | `data_cache/dashboard` | job history, settings and dry-plan files |
| `--allow-model-keys` | off | pass `ANTHROPIC_API_KEY` / `ODDS_API_KEY` to dry plans (they can cost money) |

## First run

1. Start the dashboard. With no database yet, every page tells you what to do next.
2. **Agent Operations → Refresh data** downloads public FPL data (prices, news, fixtures, official
   points) into the database. You can also use `fpl-agent db-update --points`.
3. **Agent Operations → Settings**: enter your FPL team id (the number in your FPL points-page URL).
   Season, horizon (1-8 GWs) and simulations (50-5000) can be changed too.
4. **Agent Operations → Dry plan** runs the weekly plan in dry mode. Afterwards, AI Decisions,
   My Team and Gameweek Planner show it.
5. **Agent Operations → Review** scores the latest finished gameweek.

## Sections

| Section | Shows | When data is missing |
|---|---|---|
| Overview | season and next GW, snapshot age, first kickoff (local time), latest plan, stored counts | setup guidance; account points, rank, team value and exact deadline are explicitly unavailable |
| My Team | the interactive football pitch with latest planned XI, bench order, captain/vice, xP and 10-90% range | the last deadline's public squad and your current squad are not stored (the latter needs an FPL sign-in, which the dashboard never does) |
| AI Decisions | each stored plan: transfers, captain, chip, hits, bank, warnings, the projection run it used | older live rows are labelled "outcome not recorded"; only `live-done` rows are "sent and verified" |
| Player Analytics | search, position filter, sort, paging, compare 2-4 players, detail dialog with form and recent-GW chart | unprojected or unknown values show "—", never 0 |
| Gameweek Planner | projection horizon, planned later moves, held chips, fixtures filtered by club/GW, unscheduled fixtures | no invented future decisions |
| Agent History | dashboard jobs, recorded agent runs with status/state/entry, decisions, reviews, lessons, data loads | older decisions may predate run tracking |
| Agent Operations | settings and the three actions, job output, cancel | buttons are disabled while a job runs |
| Data Health | snapshot age (stale after 6 h), last successful load per source, incremental state, settings presence, table sizes | the database records successful loads only |

## Safety

- Listens on 127.0.0.1 only and rejects any other `Host` (protects against DNS rebinding).
- A POST must come from the page itself (`Origin`) and carry a token generated each time the
  dashboard starts; bodies are limited to 4 KB. GET requests never change anything.
- Only three fixed actions exist: refresh, dry plan, review. Each runs the existing CLI as a
  child process with a fixed argument list (no shell). The child always gets
  `AGENT_MODE=dry`; FPL sign-in, GitHub and webhook credentials are removed from its
  environment, and notifications are off. There is no live-transfer, token, shell,
  deployment or secret button.
- One job at a time. The lock is `writer.lock` in the state folder, so a second dashboard on
  the same folder is refused too. Jobs stop after 30 minutes (review: 10) and can be cancelled.
  A job left running when the dashboard stopped is shown as "interrupted" after a restart.
- Settings are stored in `data_cache/dashboard/preferences.json`. They contain no secrets and
  do not change the GitHub Actions configuration.
- Settings and keys appear as "set" / "not set", never by value. Pages insert every value as
  text, so news or player names cannot inject HTML. Strict Content-Security-Policy; framing
  is forbidden.

## How data is read

The database is opened read-only just long enough to build one consistent snapshot of every
section, then closed, so a CLI run or a dashboard job can write. The snapshot stays in memory
and is rebuilt only when the database file changes and no job is writing. While a job runs, or
while another program holds the database, the last good view is shown with a notice. In the
tests, cached API reads stay well under 500 ms (p95). The first snapshot of a database is
slower; its build time is returned as `meta.build_ms`.

## Scheduling, backups and limits

- The dashboard is not a scheduler. Scheduled runs still come from GitHub Actions. Locally,
  use Task Scheduler or cron with `fpl-agent db-update --points` and `fpl-agent plan`, or press
  the buttons by hand before each deadline.
- Back up by copying `data_cache/football.duckdb` while nothing is running (stop the dashboard
  or wait for its job to end). Restore by copying it back.
- Single user, local machine. Remote or multi-user hosting is not supported.


## Interface and data limits

The desktop sidebar becomes a horizontal navigation strip on phones. Player names and pitch
cards open a keyboard-accessible dialog (Escape closes and restores focus). Select two to four
players to compare; charts use stored official match points or clearly labelled projections.
Recent history sums fixtures per gameweek, including double gameweeks. All times use your
browser's timezone. Freshness is recalculated on API reads even when the database is unchanged.
Reload or revisit a page to read newer data; operations poll while a dashboard job runs.

The exact deadline, current account rank/points/value, fixture difficulty ratings and detailed
narrative reasoning are not stored. They are never estimated or invented. The displayed squad
is a stored plan, not a signed-in account view. Add your own team ID in Agent Operations and
run a dry plan to populate it. No account-changing actions are offered.

## Validation

There is no frontend compilation or asset build: packaged HTML/CSS/JavaScript are served directly.
With the existing dev dependencies installed:

```text
.venv/Scripts/python -m pytest -q tests/test_dashboard.py tests/test_dashboard_controls.py
.venv/Scripts/python -m ruff check src/fpl_agent/dashboard tests/test_dashboard.py tests/test_dashboard_controls.py tests/dashboard_fixture_server.py
node --check src/fpl_agent/dashboard/static/app.js
npm run test:dashboard
```

Use `.venv/bin/python` on Linux/macOS. Browser tests use Chromium (install once with
`npx playwright install chromium` if absent). They start a temporary isolated database on
port 8772 using recorded FPL responses and explicitly synthetic decisions/projections; they
never use your database or make FPL account changes. Default scaffold Playwright configuration
and tests are separate; `test:dashboard` selects only the local dashboard suite.

Screenshots in `artifacts/`: `dashboard-desktop.png` and `dashboard-mobile.png` show the real
local database. `dashboard-team-fixture.png` and `dashboard-team-mobile-fixture.png` show synthetic test decisions
for pitch layout validation, not real agent recommendations.

Validated on 2026-10-20: 30 Python dashboard tests, 6 Chromium browser tests; Ruff lint/format, JavaScript syntax and Python compilation passed. Screenshots were visually inspected.

Agent History also shows sanitized recorded run reasons and stage seconds, plus scout outcomes, usage and cache counts. New decisions preserve deterministic field/check context and chip timing comparisons; older records may lack these.

Injury eligibility, simulated bench cover, value watchlists, news monitoring and historical
evaluation are described in [football-intelligence.md](football-intelligence.md). Refresh now
also imports missing league/European match details. News verification shows source failures
and date quality; it never automatically clears an injury.
