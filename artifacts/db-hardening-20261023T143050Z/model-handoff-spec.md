# U09 — model-stage handoff specification (Claude, 2026-10-23; awaiting Codex)

Scope: inputs and testable gates for the next (model) stage. No model change, policy promotion or
claim of improved FPL returns is made here. Numbers cite existing evidence; "to measure" means unknown.

## Data-to-feature matrix (current season 2026 unless stated)
| Projection component | Input table(s) | Coverage / freshness (evidence) | Temporal eligibility | Main uncertainty |
|---|---|---|---|---|
| Minutes model | `fpl_points_gw` (history), `fpl_players_gw` (snapshots) | 4,551 current-season rows; own-GW snapshot only from GW8 (F-17) | position: season fill now point-in-time (`positions_as_of`); price/status/team/chance NULL without own snapshot | trains without price features for GW1-7; 0.3 flat position prior where `pos_rate` is empty |
| Attacking rates | `match_frame` (FotMob), `fpl_points_gw` stats | rate sources at GW8: 277 pl / 27 translated / **364 prior** of 668 (weekly T04) | match rows `date < as_of` | 68 regulars on priors despite FPL `expected_goals`/`expected_assists` (O01) — unused validated input |
| Defensive / CBIT | FotMob `cbit`, FPL `defensive_contribution` | FPL field present on played rows (DB02 follow-up 3) | as above | FotMob vs FPL definitions differ; F-16(b) xG-sum disagreements unresolved |
| Team strength | `matches`, `team_match`, `strength.load_matches(as_of)` | full EPL history | `as_of` date | promoted-team priors |
| Odds | `match_odds_log` (append-on-change, prospective), `match_odds` | log only from its introduction; earlier prices have unknown observation time | `ingested_at <= cutoff`; closing never used with a cutoff | backtests before the log use `known_at=False` reconstruction (labelled) |
| Ownership / EO | `field_ownership` | 200-manager sample; 512 players absent = unknown, not 0% (weekly) | `fetched_at` | sample bias toward top managers |
| Availability | `fpl_players_gw.status/chance_next/news`, `news_evidence` | FPL flags only for the squad; no club evidence stored (weekly) | snapshot `<= cutoff` | late team news after the last snapshot |
| Deadlines | `fpl_deadlines` (new, versioned) | prospective from 2026-10-23 | `observed_at <= as_of` | past seasons: first-kickoff − 90 min approximation |

## Frozen inputs for experiments
- Data: `artifacts/db-hardening-20261023T143050Z/work-snapshot.duckdb` (sha256 92ce9f1a…, 30 tables) — never mutate;
  copy per experiment with `snapshot.py`.
- Code: record `code-files.sha256` style fingerprint per run; seed 0, 1000 samples (weekly convention).
- Baseline decision output: `artifacts/deadline-20261023/candidate/` (weekly) — reference only; not re-recommended.

## Experiments E0–E5 (each: one change, everything else frozen; gates set before running)
- **E0 reproducibility**: rerun baseline twice → byte-identical `gw*.json` (already shown once in weekly T05). Gate: identical.
- **E1 FPL-stat rates for prior players (O01)**: use FPL `expected_goals/assists` per 90 where FotMob is missing.
  Gate: leakage-safe replay over GW2–7 (cutoff per GW, `positions_as_of` applied), calibration of goal/assist
  probabilities no worse on held-out GWs; report the number of players moved off priors.
- **E2 minutes without snapshot features vs with** (F-17 impact): same replay; gate on minutes log-loss / Brier.
- **E3 ownership lock / memory policy**: on vs off; weekly T04 showed no effect on that decision — need ≥6 GWs of
  replay before any claim. Gate: no change in median points; report variance.
- **E4 strong-core vs differential**: captain choice by EV vs by EV−λ·risk; report P(0) and rank-delta proxies only.
- **E5 C02 lineup coherence** (>10 outfield starters per club per sim): fix in simulation, gate = impossibility rate 0
  with unchanged marginal start probabilities (±0.01).
- Cutoff-invariance gate for every experiment: adding later observations to the DB copy must not change any earlier
  GW's projection (extend `test_season_position_fill_is_cutoff_invariant` style tests to the experiment's inputs).

## Not in scope / blocked
- F-16 adjudication (needs independent match reports and raw payloads, not retained).
- Any claim of better FPL returns; any live-mode or production policy promotion.
