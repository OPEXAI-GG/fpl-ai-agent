# Website demo (Matchplan)

A read-only website on top of the engine. A visitor enters an FPL team ID or team link. The
site shows a dashboard, this week's recommendation compared across every plausible
free-transfer count, fixtures, player forecasts, the model's track record and their settings.
There is no sign-in, no FPL account connection, no screenshots, no payment and no Discord.

The site never changes a team. It reads FPL's public API and the engine's football database,
and it calls the same solver, comparison and rails the weekly agent uses, in dry mode.

## Launch

```bash
scripts/web_demo.sh                    # install, prepare data if needed, start everything
DEMO_PUBLISH=1 scripts/web_demo.sh     # also publish a fresh data revision first
```

Then open <http://localhost:3000>, or <http://localhost:3000/team/sample> for the labelled sample
team. The script needs `uv`, Node.js 20+ and, on the first run, a football database:

- **Database already there** (`data_cache/football.duckdb`): it is used as is.
- **No database**: the script restores the latest export from the `data` release with
  `scripts/data_restore.sh`, which needs `gh` signed in to this repository, then imports it.
  Without `gh`, build one with `uv run fpl-agent db-update --points`.
- **Publishing**: `fpl-agent web publish --ownership` copies the database into an immutable
  revision, records FPL's bootstrap and fixtures with it, takes the top-10k ownership sample,
  rebuilds the sample team and queues the shared forecast. It reads public FPL data only and
  sends no notifications.

The services can also be started one at a time:

| Command | What it runs |
|---|---|
| `fpl-agent web serve --port 8000` | the HTTP API (FastAPI) |
| `fpl-agent web worker` | a worker for forecasts and plans (start one or more) |
| `cd web && npm run build && npx next start` | the site; `API_URL` points it at the API |
| `fpl-agent web status` | current revision, forecast and queue |
| `fpl-agent web forecast` | build the shared forecast now instead of in a worker |
| `fpl-agent web challengers` | evaluate the shadow challengers (shown on the model page) |

Settings are environment variables with safe defaults; see [`web/.env.example`](../web/.env.example).

## Architecture

```
browser ── Next.js (web/) ── /api/* proxy ── FastAPI (fpl_agent.web.api)
                                               │  reads: SQLite app store, provider cache,
                                               │         immutable data revisions, forecasts
                                               └─ enqueues jobs ─► worker(s) (fpl_agent.web.worker)
                                                                   forecast → plan (engine, dry)
```

- **Shared data and personal state stay separate.** Revisions (`data_cache/web/snapshots/`) and
  forecasts (`data_cache/web/forecasts/`) are shared and never hold anything about a visitor.
  Per-visitor state is an anonymous session row in SQLite: the selected team and preferences.
  A session never proves ownership of a team, and nothing about one visitor is visible to another.
  Job status shows only to the session that asked, or to visitors looking at the same public
  team. Session ids are never returned.
- **No concurrent DuckDB writes.** `publish` opens the working database read-only, copies it,
  checks the copy matches, and renames it into place atomically. Requests and workers open
  revisions with `FootballDB.read_only`. Training, refreshes and publishing never run from a
  page request.
- **Point in time.** A revision's cutoff is the moment it was copied. The forecast uses
  `current_pool(as_of=cutoff)` and the engine's projection, with the same feature modes and
  calibration as the agent (`agent.active_config`). The owner's lessons memory is excluded,
  because it is single-manager evidence.
- **Forecast cache.** Key: revision, cutoff, gameweeks, samples, seed, model version, feature
  version, modes, calibration and learning version. The arrays are memory-mapped and shared by
  every visitor.
- **Plan cache.** Key: forecast key, squad-state fingerprint and preferences (strategy and
  target rank). A new revision or forecast, a changed public squad or new preferences give a
  new key, which is how a plan is invalidated.
- **Jobs.** These are persistent in SQLite. Identical requests share one active job. Forecasts
  run before plans. A plan waiting for its forecast is requeued. A stale job (no heartbeat for
  30 minutes) is retried once, then failed. Every job ends `done`, `failed` or `timed_out`, and
  a timeout is labelled as such, never shown as success.
- **Limits.** All visitors together get 120 outbound FPL requests a minute. A forced refresh is
  allowed once per team per 5 minutes and 3 times per session. Each session gets 20 lookups a
  minute and 12 plans an hour, and the queue is capped at 20.
- **Provider failures.** The gateway retries with backoff, then serves the last cached copy,
  labelled stale with the error. With no copy it answers `provider_unavailable`. The sample team
  is never substituted for a failed lookup.

## What the plan does

1. **Most likely free-transfer balance**, without the chip search. This is labelled
   *preliminary* and appears first.
2. **Every other plausible balance**, from `ft_scenarios()` and including 0, with the same
   forecast, thresholds and simulated worlds. When the advice differs between balances it is
   shown as conditional ("if you have N…"). Otherwise it is "holds whether you have …".
3. **The chip search** on the most likely balance. This is labelled *final*.

The recommendation uses `solver.decide`, with `compare_counts` set to `adaptive=True`. An option
whose result lands within 5 points of win share, or 0.5 points of gain, of a threshold is
rescored in every sampled world, and roll is rescored in the same worlds. The thresholds are
unchanged: 55% win share, +1.0 point. The comparison shows each option's gain with its 10th to
90th percentile range.

The proposed lineup is passed through `execution.safety.check` in dry mode. If the rails would
reject it, the page shows the reason instead of the lineup.

**Strategies.** The forecast (expected points) is the same in every strategy. Only the treatment
of other managers' ownership changes.

| Strategy | What changes |
|---|---|
| Points | No ownership protection and no rank risk. |
| Balanced | Popular players are kept unless unavailable, plus the agent's rank-risk term. |
| Rank | Balanced, plus chasing a target rank via `rank_target.chase`. If the standings can't be read, it falls back to balanced and says so. |

**What public data cannot tell.** The public data shows the squad and bank at the last
deadline. It does not show transfers made since then (they are public only after the next
deadline), the exact free-transfer count (estimated from history) or exact selling prices
(estimated with the half-profit rule). Each of these is labelled *verified*, *as of last
deadline*, *estimated*, *conflicting* or *unknown*.

## Measured timings (7 Nov 2026, 4 CPUs, GW11, one real team)

| Step | Time |
|---|---|
| Shared forecast (1,000 worlds, 8 gameweeks) | 48–55 s, once per revision |
| Lookup (API, cached) / dashboard first load (API) / dashboard cached (API) | 0.04 s / 0.45 s / 0.08 s |
| Dashboard in the browser, first visit / reload | 0.2 s / 0.7 s |
| Plan: preliminary (most likely balance, adaptive comparison) | 100 s |
| Plan: every free-transfer balance | 193 s |
| Plan: final, with the chip search | 545 s |
| Plan already computed (any visitor, same squad and settings) | instant (cached) |
| Model performance page (first, then cached per revision) | 1.8 s, then about 0.05 s |

The chip search dominates. It is the engine's own search: up to 4 chips by 8 gameweeks, with a
20-second limit per scenario. It was not shortened for the website. Under CPU contention (two
plans plus a forecast at once) a final plan took 18 minutes. Run at most one or two workers per
4 CPUs.

## Model improvements: status

| Improvement | Status | Evidence |
|---|---|---|
| Adaptive rescoring of borderline transfer counts | **Active on the website only** (the agent is unchanged: `adaptive=False` by default) | Same thresholds and worlds; only more of them where the call is close |
| Gain uncertainty (sd, 10th–90th percentile) in the comparison | **Active** (display only) | From the same worlds |
| Weekly-block check on feature promotion | **Active** in the learning gate (stricter only) | A feature now also needs its gain to hold across whole gameweeks, not just across correlated player-weeks |
| Direct points challenger (gradient boosting) | **Shadow** | Backtest 2025/26: −0.075 MAE against FPL's recorded xP (± 0.030 over 38 GWs). Replay on 2026 GW6–9 issued-forecast cohort: +0.141 ± 0.118 against the simulator (no difference established; the blend chose the simulator alone) |
| Minutes challenger (start / 60+ / cameo / none) | **Shadow** | Backtest 2025/26: P(start) Brier −0.016 ± 0.001 against last-5 frequencies. No forward comparison yet, because the issued GW6–9 forecasts did not store P(start) |
| Shrunk per-90 xG/xA (contribution) | **Shadow** (challenger feature) | Ablation: no squared-error difference established |
| Opponent-adjusted form | **Shadow** (challenger feature) | Ablation: no squared-error difference established |
| European workload | **Shadow** (engine feature and challenger feature) | Ablation: no squared-error difference established |
| News and social signals | **Not used** | Dropped by decision |
| Ownership and availability flags in older seasons | **Unavailable** | Not in the historical snapshots |
| npxG | **Unavailable** | FPL's `expected_goals` includes penalties |

The challengers are refitted only on earlier data. A test rewrites a gameweek's results and
checks that no feature of that or any earlier gameweek changes. Uncertainty is a standard error
over per-gameweek means. Promotion goes only through the engine's learning gate, with its fixed
thresholds; nothing on the website promotes a model.

## Model performance page

The page scores only issued forecasts: one per gameweek, made before the deadline, picked by
`pit.eligible_run`. It compares them with FPL's own pre-deadline expected points and a last-5
average, on identical players. On GW6–9 (2,670 player-gameweeks), the model's MAE was 1.088,
against 1.194 for FPL xP and 1.208 for the last-5 average. Against FPL xP the difference is
−0.106 ± 0.067 across gameweeks: no difference established. Against the last-5 average the
model is lower by more than 2 SE. The GW6–9 forecasts were stored before the run registry
existed (legacy runs), and the GW9 forecast was issued 8 days early. Backtests are shown
separately and labelled as such.

## Tests

- `tests/test_web_units.py`: ID and link parsing, the store (sessions, dedupe, claims, stale
  recovery, limits), gateway caching, failures and budgets, the sample gateway, read-only
  databases.
- `tests/test_web_flow.py`: a synthetic world and fake FPL end to end through the API and
  worker. Covers two-visitor isolation, dedupe, conditional plans, legal lineups, determinism,
  invalidation, timeout labelling, the chip stage, the sample team and the CLI.
- `tests/test_challengers.py`: leakage, outcome classes, uncertainty units and verdicts.
- `web/e2e/journey.mjs`: browser journey at desktop and phone sizes. Checks keyboard use,
  overflow, contrast, console errors and failed calls, and saves screenshots. Run
  `ENTRY=<id> npm run e2e` with the demo running.

## Limitations

- Plans for a real squad take minutes, not seconds. The preliminary result arrives first, and a
  plan is cached for every visitor with the same squad and settings.
- Free transfers, current-week transfers, bank and selling prices are estimates until FPL
  publishes the next deadline. The plan is computed for every plausible free-transfer count for
  that reason.
- This is a demo for one machine: the store is SQLite and the workers are local processes.
  There is no production deployment, billing or account system.
