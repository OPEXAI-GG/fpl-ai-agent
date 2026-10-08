# Architecture

The agent turns public football data into one weekly decision: transfers, chip, starting XI and
captain. Layers are separate modules with one-way dependencies, each tested on its own.

```
sources ──► L1 database ──► L2 team strength ─┐
 FPL API                      L3 minutes       ├─► L5 match simulator ─► projection ─► L6 planner ─► L7 rails ─► execute
 FotMob (15 comps)            L4 player rates ─┘        ▲                                 │ chips          │
 Understat                                              │ odds (optional)                 ▼                ▼
 football-data.co.uk                                    │ scout (optional)           decision          dry report / (live)
 vaastav archive (2016→)                                                                  │
 The Odds API (optional)                                                                  ▼
                                                                         L8 review: prediction error, decision regret
```

| Layer | Module | What it does |
|---|---|---|
| L0 rules | `rules/`, `rules/2026_27.yaml` | Scoring, bonus (with tie rules), auto-subs, selling prices, chip windows. All numbers come from the YAML, so a rule change is a data change. Reproduces FPL's official points on recorded gameweeks. |
| L1 data | `data/`, `db/` | DuckDB football database (matches, team xG, appearances, per-player stats incl. defensive-contribution proxies, shots, odds, FPL snapshots and points, projections, decisions, reviews). Canonical team/player ids across sources, point-in-time reads, parquet export. Contract checks on every source response, quality checks before export. |
| L2 strength | `model/strength.py`, `model/odds.py` | Dixon-Coles attack/defence ratings with time decay and a tier prior across leagues; bookmaker odds (when a key is present) are de-margined and blended as goal rates. |
| L3 minutes | `model/minutes.py` | Gradient-boosted P(start), P(60+), cameo minutes from recent usage, rest, price and position; availability overlay from FPL's status/chance flags with a recovery hazard over the horizon. |
| L4 player rates | `model/players.py`, `model/translation.py` | Shares of team xG/xA, finishing, DefCon (negative binomial), cards - with shrinkage to position priors. Rates from other leagues are translated to Premier League level with factors estimated from players who moved between leagues. |
| L5 simulator | `model/simulate.py`, `model/project.py` | Vectorised match simulation: a shared team goal intensity, goals/assists allocated to players on the pitch, clean sheets, saves, DefCon, BPS and the 3-2-1 bonus with FPL's tie rules. Output is a distribution of points per player per gameweek (doubles sum, blanks are zero), so risk and upside are first-class. |
| L6 planner | `solver/planner.py`, `solver/chips.py`, `solver/decide.py` | A MILP (HiGHS) over a 6-gameweek horizon: legal squad every week, selling prices, three-per-club, free-transfer banking to five (a banked transfer is valued by how many are banked), hits only if they buy `4 + buffer` points. Chips are scenarios (one solve per chip x gameweek) judged on expected points *and* on how often they win across sampled worlds; a Wildcard needs 70%. Windows that close inside the horizon make a chip must-use. |
| L7 safety + execution | `execution/` | Rails (kill switch, stale data, hit caps, availability, chip guard, transfer count, budget, legal squad, human edits not overwritten) run on our plan and again on FPL's preview. **Dry mode makes no authenticated request at all.** Live path: refresh-token exchange with rotation write-back, preview -> confirm -> lineup -> read-back verify. |
| L8 review | `review.py` | After each gameweek: prediction error by position and by scoring component, top-20 calibration, drift flags; decision review (captain regret, transfer gain after hits, XI points vs. predicted). Written to the `reviews` table. It reports and flags; it never edits a model by itself. |
| Scout | `scout.py` | One bounded Claude call over players with injury/news text. Output is validated, can move a player's availability at most 35 points from FPL's own flag, and never revives a player FPL lists as gone. No key = no-op. |
| Orchestration | `agent.py`, `cli.py`, `.github/workflows/` | `fpl-agent plan` runs the whole chain; workflows keep the database fresh, plan in dry mode and review. |

## Key design decisions

* **Points are built the way FPL scores them**, position by position, rather than regressing "points" directly. That makes components checkable (the review loop compares each one with FPL's `explain` data) and lets a rule change flow through without retraining.
* **Distributions, not point estimates.** Every decision can ask "how often would this have been better?" - this is what gates the Wildcard and what captain choice can use.
* **Skip safely.** A failing optional source (odds, scout, FotMob endpoint) is skipped and reported; only a failing critical source (FPL) aborts, and then nothing is written.
* **No look-ahead.** Every model sees only data before the gameweek's first kickoff (`model/backtest.py` replays this).
* **Dry by default, in three places.** `Settings.mode` defaults to `dry`; `execute()` returns before any credential is touched unless mode is `live`; the plan workflow pins `AGENT_MODE: dry` in the file.

## Data in the database

Tables: `competitions teams players matches team_match appearances player_match_stats shots match_odds fpl_players_gw fpl_points_gw projections decisions reviews league_translation ingest_log ingest_state` plus id maps. The database is rebuilt from sources; it lives as parquet files in a `data` release asset rather than in git.

## Conventions

* Positions `GKP/DEF/MID/FWD`; seasons are start years (2026 = 2026/27); money in tenths of £m.
* Competition ids: `ENG1 ENG2 ENG3 ESP1 ITA1 GER1 FRA1 NED1 POR1 BEL1 SCO1 BRA1 UCL UEL UECL`.
* Each source has recorded real responses under `tests/fixtures/` and a contract in `contracts.py`; a daily canary workflow checks the live sources still match.
