# Workload factor experiment (`minutes-workload-v0`)

Owner: Nabil (nabil-mvsc2c3l), 2026-10-30. Isolated and switched off by default.
- **Not changed:** `sim-v1`, the default model, shared source, the production DB, config and the
  FPL account.
- **Not wired in:** nothing in the pipeline imports this code.
- **No performance claims:** 2026 has 0 eligible gameweeks, so no lift or best combination is
  claimed. Only structural and cutoff checks were run.
- **Review:** handed to Wegz.

## Files

| File | What |
| --- | --- |
| `workload.py` | Executable factor calculation from a DB (`.duckdb`, read-only) or a Parquet export. Writes `features-<season>.csv` and `coverage-<season>.json` |
| `checks.py` | Structural and cutoff checks on an in-memory copy (exit 1 on failure) |
| `CONTRACT.md` | Data contract: inputs, time semantics, definitions, missingness, limits |
| `comparison-plan.json` | Predeclared comparison plan (not run) |
| `out/` | Results on the production export r20261028 |
| `wegz-review.md` | Wegz's review of r1 (CHANGES NEEDED: C1, C2, P1), addressed in r2 below |
| `checks-output.txt` | Check results for GW6, GW7 and GW8 |
| `source-hashes-before.txt` | Parquet hashes of the source; unchanged after all runs |

**Reproduce from the repo root:**
```
S=artifacts/FAA-7-production/r20261028/export
PYTHONPATH=src .venv/Scripts/python.exe artifacts/model-factor-workload-20261030/workload.py --source $S --season 2026 --out <dir>
PYTHONPATH=src .venv/Scripts/python.exe artifacts/model-factor-workload-20261030/checks.py --source $S --season 2026 --gw 8
```

## Appearance coverage (verified on the production export r20261028)

- **Source:** the `appearances` table is entirely FotMob lineups.
- **Competitions:** 15 in total:
  - English tiers 1–3;
  - top divisions of Spain, Italy, Germany, France, the Netherlands, Portugal, Belgium,
    Scotland and Brazil;
  - the Champions League (UCL), Europa League (UEL) and Conference League (UECL).
- **Not in the database:** domestic cups and international matches.
- **Volume:** 2026 has 80 Premier League (ENG1) matches with 3,199 appearance rows. UCL, UEL and
  UECL have 54, 54 and 36 matches.
- **Missing values:** no `minutes`, `started` or `kickoff` value is NULL in 2025–2026.
- **No ingestion before 9 Oct:** the earliest 2026 `ingested_at` is 2026-10-09. Every
  appearance before then was backfilled. The median lag from kickoff to ingest across 2026 is
  665 h.
- **Linking:** 546 of the 667–668 FPL players (81.7–81.9%) have a FotMob identity. Links are by
  name only, as FAA-7 recorded.
- **Identity collisions:**
  - 67 player-days in 2026 show one linked player with minutes in two matches on the same day,
    for different clubs (e.g. League One and Premier League at the same kickoff). These are
    namesakes merged by the name links.
  - The feature keeps only rows for the player's FPL club. European matches use the same club
    id: 120 of 128 UCL rows and 77 of 87 UEL rows match the FPL club.

## Per-gameweek coverage (season 2026, `out/coverage-2026.json`)

| GW | Class (eligibility.py) | Cutoff (kind) | Population | Linked | Link-suspect, NULL (obs / rec) | Other-club rows dropped (players) | 30-day rows | Observed by cutoff | Players with minutes in 14 days | Repair proof |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 1–5 | unavailable | approximate | no observed snapshot by the cutoff, so no rows | | | | | | | |
| 6 | reconstructed | 10-10 10:00 (approx.) | 667 | 546 | 60 / 66 | 153 (69) | 733 | 733 (100%) | 0 | none (no repair_log) |
| 7 | reconstructed | 10-17 10:00 (approx.) | 667 | 546 | 69 / 69 | 178 (71) | 838 | 838 (100%) | 275 | none |
| 8 | reconstructed | 10-23 17:30 (approx.) | 668 | 546 | 68 / 68 | 187 (70) | 943 | 943 (100%) | 320 | none |

- **GW6 has 0 players with minutes in the last 14 days.** The calendar has no covered match
  between 21 Sep and 9 Oct in any league; matchdays are contiguous, so this is a break, not a
  data hole. International matches in that break are not in the data, so GW6 workload is an
  undercount.
- **Observed vs reconstructed:** `obs_` equals `rec_` for every player in GW7–8. In GW6 they differ for 6 players, whose other-club rows were loaded after the cutoff; those players are suspect only in `rec_`. The appearance
  rows were in the database (last written) before each cutoff. They were backfilled on 10-09,
  the day before the GW6 cutoff.
- **Still exploratory:** each gameweek is labelled exploratory because its class is
  `reconstructed`, since no deadline was recorded (FAA-48).
- **Missing values among trusted linked players** (486, 477 and 478 in `obs_`): count features
  are never NULL. `rest_days` is NULL (`no_recent_30d`) for 187, 170 and 158 of them: squad
  players with no minutes in 30 days.
- **Link-suspect players:** all features NULL, so wrong links are no longer scored as rest
  (r2, C1).
- **Unlinked players:** all features NULL.
- **Population by position:**
  - linked: MID 241, DEF 180, GKP 63, FWD 62;
  - unlinked: MID 57–58, DEF 37, FWD 17, GKP 10.

## r2 changes (after Wegz review)

- **C1:** link-suspect players get NULL plus a flag, never 0 / rest. New check 6 and a new cohort.
- **C2:** rows a journalled repair rewrote after C are excluded from `obs_`. Without
  `repair_log`, `repair_proof` is `none` and the gameweek stays exploratory. New check 7. The
  contract is amended.
- **P1:** the plan now declares one validated route (`obs_` training on eligible gameweeks only,
  with repair proof) and one exploratory route (`rec_` training, `obs_` evaluation), which never
  counts as validated. The unlinked and link-suspect cohorts have an explicit 1% limit.

## Checks (`checks-output.txt`, all exit 0)

| Check | GW6 | GW7 | GW8 |
| --- | --- | --- | --- |
| 1. Events inside the 3 h buffer, after the cutoff, or in the target week change nothing | PASS | PASS | PASS |
| 2. A pre-cutoff row re-stamped as ingested after the cutoff leaves only `obs_` | SKIP (no same-club row in 7 days: break) | PASS | PASS |
| 3. A row for another club changes nothing | PASS | PASS | PASS |
| 4. Unlinked and suspect players get NULL; trusted linked players' count features are never NULL | PASS | PASS | PASS |
| 5. Determinism | PASS | PASS | PASS |
| 6. A linked player with no rows plus an other-club row becomes suspect, all NULL; no one else changes | PASS | PASS | PASS |
| 7. A journalled repair logged after C drops the row from `obs_` only; the same entry logged before C changes nothing | SKIP (break) | PASS | PASS |

- **Repeat runs:** two full runs give byte-identical `features-2026.csv`
  (`bb3e649c7147723f2e0ba719cf142f6af791c2326936d020d47607b64273b5ae`).
- **Source unchanged:** source hashes are unchanged.
- **Lint:** ruff clean.
