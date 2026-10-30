# Data contract: `minutes-workload-v0`

## Unit and key

- **One row** per (season, gw, fpl_id) for every player in the decision snapshot.
- **Decision snapshot:** the one observed FPL bootstrap pull that `db/eligibility.gameweek` uses,
  i.e. the latest pull with `snapshot_at` ≤ C whose raw JSON has `element_type`.
- **No snapshot:** a gameweek without one has no rows.

## Cutoff C

- C is the `cutoff` from `db/eligibility.gameweek`, unchanged:
  - the official deadline, if a `fpl_deadlines` row was observed before it passed;
  - otherwise the first kickoff of the gameweek − 90 min, labelled `approximate`.
- The gameweek's `class` is carried into the coverage output. Only `eligible` gameweeks may
  ever enter a validated comparison.

## Inputs (read-only)

| Table | Columns used |
| --- | --- |
| `fpl_players_gw` | `season, gw, snapshot_at, fpl_id, player_id, team_id, position, raw` |
| `appearances` | `match_id, player_id, team_id, started, minutes, ingested_at` |
| `matches` | `match_id, competition_id, kickoff` |
| `player_ids` | `source = 'fotmob'`, `player_id`: the link check |
| `fpl_points_gw`, `fpl_deadlines` (if present) | Via `eligibility.gameweek` only, for the cutoff and class |

## Time semantics

| Time | Column | Rule |
| --- | --- | --- |
| Event time | `matches.kickoff` | kickoff ≤ C − 3 h, so the match had finished by C. Matches inside the 3 h buffer, after C, or in the target week are never read |
| Observed time | `appearances.ingested_at` | For `obs_`: ≤ C. Written by INSERT OR REPLACE, so it is the **last** write. A passing row held this value at C, **except** that master's automatic repairs UPDATE `team_id`/`match_id` in place and keep the old `ingested_at` (r2, Wegz C2). See "Repairs" |
| Snapshot time | `fpl_players_gw.snapshot_at` | The loader's own `now()`, ≤ C |

- **`rec_` columns** apply the event-time rule only: reconstruction, exploratory.
- **Self-recorded times:** all times above are written by the pipeline itself; none has an
  external attestation (FAA-48).
- **Kickoff value:** `matches.kickoff` is the current stored value, not the value as of C. A
  match rescheduled after C could move in or out of a window. This has not been observed, but
  nothing prevents it.

## Repairs (r2)

- **With `repair_log` (FAA-41):** `obs_` excludes every appearance whose `repair_log` row
  (`table_name = 'appearances'`, `action = 'added'`) was logged after C.
- **Without `repair_log`:** this includes master and the r20261028 export. `obs_` is not proven
  against repairs: `coverage.repair_proof` says so, and the gameweek is `exploratory only`
  whatever its class.
- **Validated use:** a validated comparison needs `repair_proof` = `repair_log present`.

## Admissibility of an appearance

1. The player has a FotMob identity (`linked`).
2. It is for the club in the decision snapshot (`a.team_id = snapshot team_id`). Rows for
   another club are namesake collisions and are dropped and counted.
3. Its kickoff is in [C − 30 d, C − 3 h].
4. For `obs_` only: `ingested_at` ≤ C.

## Features (per variant `obs_` / `rec_`; appearances with minutes > 0)

| Column | Definition | Unit |
| --- | --- | --- |
| `mins_7d`, `mins_14d` | Sum of minutes with kickoff in (C − 7 d] / (C − 14 d] | minutes |
| `starts_7d`, `starts_14d` | Count of starts in the same windows | count |
| `apps_14d` | Matches with minutes > 0 in 14 days | count |
| `euro_mins_14d` | Minutes in UCL/UEL/UECL in 14 days | minutes |
| `rest_days` | (C − last kickoff with minutes > 0 within 30 d) | days, ≥ 0.125 |
| `no_recent_30d` | Linked, not link-suspect, and no minutes in 30 days | bool |
| `link_suspect` | Linked, with other-club rows and no FPL-club row in 30 days | bool |

## Missingness

- **Link-suspect players (r2, Wegz C1):** a player is suspect when they have rows for another
  club in 30 days but none for the FPL club (zero-minute FPL-club rows count as proof of the
  link). Every feature is NULL, `<v>_link_suspect` is True and `no_recent_30d` is False.
  Decided per variant: `obs_` uses only rows observed by C.

- **Unlinked players:** every feature is NULL. Unknown is not rest.
- **Linked players with no admissible appearance:** counts are 0, `rest_days` is NULL and
  `no_recent_30d` is True.
- **Uncovered matches are invisible**, so all counts are lower bounds:
  - domestic cups (FA Cup, EFL Cup);
  - international matches;
  - friendlies;
  - competitions outside the 15 covered.

## Identifiers

- **Feature set:** `minutes-workload-v0`.
- **Model using it:** `sim-v1-workload`, never `sim-v1`. It is not wired: nothing calls it.

## Known limits

- **Linking:** name-based (about 18% of FPL players unlinked). Namesakes can merge; the
  same-club rule removes the cross-club ones, but not a same-club namesake.
- **Transfers:** a player who moved PL club inside the window loses the old club's rows.
- **History:** backfilled 2025 and older history would be reconstruction (ingested in October
  2026).
