# M01: data-to-decision matrix

Author: Nabil (nabil-mvsc2c3l), 2026-10-28. Source: the M00 isolated copy
`work-data.duckdb` (SHA-256 `94f68833…a813`, unchanged after these read-only queries),
`m01-source-inventory.json`, `m00-data-quality-eligibility.json`, `baseline-rates.csv` and the
model code at git `c6a0c5f` plus the working tree. No network access, no production access, no
model run. Coverage numbers below describe data presence only; none of them is forecast accuracy.

## Shared facts

| Fact | Value | Evidence |
| --- | --- | --- |
| Season evaluated | 2026, GWs 1–7 played | `fpl_points_gw` |
| Eligible gameweeks for validated evaluation | **0** (GWs 1–7 unavailable) | M00 eligibility |
| Excluded matches (FPL–FotMob conflicts) | 3 (match ids 31, 40, 62) | M00 quality warn |
| FPL per-fixture rows | 4,551 rows, 668 players; **all loaded 2026-10-20 14:48–18:08 UTC**, after GW7 | ingest timestamps |
| Latest FPL squad snapshot | 2026-10-20 18:08 UTC (166.5 h old at M00; more now) | source health: blocked, "FPL snapshot stale" |
| FPL fetch attempt records | `never` = no attempt recorded, not a proven fetch failure | source health |
| FPL deadlines table | **0 rows**; the projection uses `approx_deadline` from first kickoff | inventory `fpl_deadlines` |
| Baseline rate sources (668 players) | `pl` 399, `translated` 36, **`prior` 233** | `baseline-rates-run-1.json` |
| Identity links | 424/449 players who played linked (94.4%), 97.4% of minutes; 25 unlinked (16 no FotMob name, 9 position differs) | M00 link coverage |

## Matrix

| Decision | Inputs used now | Available but unused | Missing | Exposure / freshness | Uncertainty handling | Fallback and its reason |
| --- | --- | --- | --- | --- | --- | --- |
| **Minutes** (P(start), P(60+), P(cameo)) | FPL per-fixture minutes and starts (form, zero-minute streaks, rest), price rank in team, newness (`minutes.py`) | FPL `news` text; `transfers_in/out_event` | Point-in-time injury news for past gameweeks (so availability is an overlay, not a trained feature); confirmed line-ups | 7 GWs of 2026 FPL rows, loaded 2026-10-20 | Classifier probabilities; certainty decays toward the long-run rate across the horizon | No price/team on gameweeks without a snapshot: the price features are dropped (`history_inputs`) |
| **Attacking returns** (goals, assists) | FotMob player-match xG/xA as share of the team's xG, recency-weighted, translated from other competitions, shrunk with 12 team-xG pseudo-exposure; finishing shrunk with 25 xG (`players.py`) | **FPL per-fixture expected goals and assists: present on 100% of 4,551 rows, not read by the baseline** (M02 candidate); FotMob shots and key passes | FotMob xG on only 2,425 of 6,862 match-frame rows (npxG 1,027 of ~2,017 PL rows); no set-piece share beyond the snapshot penalty order | PL detail 70/70 finished matches; FotMob loaded 2026-10-20 20:07 | Poisson simulation; shrinkage toward position average | **233 players (35%) on the pure position prior**: no linked detailed history, or no minutes. Reasons: unlinked identity (25 who played), no FotMob rows, or no 2026 minutes |
| **Defensive contributions** (DefCon) | FotMob CBIT (DEF) / CBIRT (others) per 90, negative binomial, 600-minute prior | FotMob blocks, clearances, interceptions, recoveries, tackles separately | Official FPL DefCon counts are in the `stats` JSON, used for discipline; not used for the rate itself | ~2,017 PL player-match rows | Negative-binomial dispersion per position | Position prior when no linked history |
| **Team strength** (goals for/against) | `team_match` goals and xG, 0.6 xG / 0.4 goals blend (`strength.py`); PL 70 matches × 3 sources; UCL 38, UEL 36, UECL 18 matches (FotMob) | Shots, corners | **Prior seasons are not in this copy** (earliest match 2026-08-21), so ratings rest on 7 GWs | Last match 2026-10-20 | Rating fit; Poisson goal matrix | Odds blend weight 0.5 where usable (next row) |
| **Market (odds)** | `match_odds` football-data: 280 rows, 70 matches (2026) | — | **`match_odds_log` has 0 rows**: no point-in-time odds, and closing odds are never used with a cutoff, so the market blend is inactive for any cutoff projection | 2026-10-20 | — | Model-only lambdas |
| **Availability** | Latest snapshot `status` (668/668) and `chance_next` (339/668); 204 players not status `a`; return-hazard overlay (`minutes.availability_factor`) | `news` text | Point-in-time status for past gameweeks (only 2 snapshot times exist) | Snapshot 2026-10-20 18:08 (stale) | Hazard by status, decays across the horizon | `a` with no chance = fully available |
| **Ownership / popularity** | `field_ownership` GW8: 156 players, sample of 200 top managers, `owned` and `eo`; fetched 2026-10-20 15:13. Used **only by the solver** (`solver/decide.py`: template floor, captain effective-ownership risk) | `selected_by_percent` in snapshots (overall ownership) | Field ownership for other gameweeks; ownership by rank tier | One gameweek only | None (point estimate from 200 managers) | Solver runs without the rank layer when `eo` is absent |

Popularity stays out of the expected-points model: `project.py` only copies
`selected_by_percent` into the output metadata column `owned` (lines 73, 356) for the solver; no
ownership field feeds `players.py`, `minutes.py`, `strength.py` or the simulation (checked by
search).

## Expected-points core and differentials (definition only, not implemented)

- **Core:** the squad chosen on expected points alone, from the projection above.
- **Differential candidate:** a player outside the core whose ownership is below a stated
  threshold (from `field_ownership.eo`, or `selected_by_percent` when the field sample is
  missing). It is shown with three numbers: **expected-points cost** (core replacement's expected
  points minus the candidate's, over the same horizon), **uncertainty** (the simulation's
  10th–90th percentile spread for both), and the **ownership estimate with its sample size**. Low
  ownership alone never earns a place; a candidate with a positive cost is labelled as a rank
  gamble, never as an improvement, and no weekly gain is promised.
- Today's limits: ownership exists for GW8 only from 200 managers, and 35% of players use the
  position prior, so their differential spread is mostly prior.

## Why M02 targets the FPL expected-goals fallback

The largest validated gap is attacking returns for the 233 prior-only players, while FPL already
supplies per-fixture expected goals and assists for every row. Using it only where detailed
history is absent, per stat, cannot double count the FotMob source.

**Limit for evaluating M02:** all FPL per-fixture rows were loaded on 2026-10-20, so at any
honest past cutoff (before GW8) the fallback correctly finds nothing usable and equals the
baseline. Any past-gameweek comparison must treat the FPL rows as if known at kickoff, which is a
reconstruction: exploratory only. A fresh snapshot cannot change this for GWs 1–7.
