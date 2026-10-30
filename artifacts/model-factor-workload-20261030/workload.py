"""Candidate feature set `minutes-workload-v0`: recent competitive workload and recovery time of
each FPL player before a gameweek's decision cutoff. Isolated experiment: nothing in the
pipeline imports it, `sim-v1` and the default model are untouched, and the database is opened
read-only (or a Parquet export is read through in-memory views).

For gameweek k, cutoff C = the cutoff `db/eligibility.gameweek` uses (official deadline if
recorded before it passed, else first kickoff - 90 min), and the population = the players of
the one observed FPL bootstrap pull it uses (latest observed `snapshot_at` <= C).

A player's appearance (table `appearances`, FotMob lineups, every covered competition) counts
only when its match had **finished by C**: kickoff <= C - FINISH_BUFFER (event time), and only
when it was for the club the FPL snapshot lists (`team_id`; European ties use the same club id).
Rows for another club are identity collisions from name-based linking and are dropped and
counted. A linked player whose window holds rows for another club but none for the FPL club
is a suspect link (`link_suspect`): all their features are NULL, never 0. Two variants are
computed side by side:
- `obs_*` (observed): also requires the row's `ingested_at` <= C. `appearances` is written with
  INSERT OR REPLACE, so `ingested_at` is the time of the row's last write; a row that passes
  was in the database, with this value, at C, unless an automatic repair rewrote its
  `team_id`/`match_id` in place after C (master's repairs keep the old `ingested_at`). When the
  source has `repair_log` (FAA-41), rows a repair re-wrote after C are excluded from `obs_`;
  without it the repair exposure is unproven and is reported per gameweek
  (`repair_proof`). Only this variant may ever feed an eligible evaluation.
- `rec_*` (reconstructed): event time only. What a backfilled database says now about the
  past; exploratory only.

Features (per variant), over appearances with minutes > 0 unless noted:
  mins_7d, mins_14d   minutes in the 7 / 14 days before C
  starts_7d, starts_14d
  apps_14d            matches with minutes > 0
  euro_mins_14d       minutes in UCL/UEL/UECL in the 14 days before C
  rest_days           days from the last appearance's kickoff (within 30 days) to C; NULL when
                      none in 30 days (`no_recent_30d` = True)
A player with no FotMob identity (`linked` = False) gets NULL features, never 0: unknown is not
rest. Domestic cups are not in the database, so workload is a lower bound (see contract).

Usage (repo root):
  PYTHONPATH=src .venv/Scripts/python.exe artifacts/model-factor-workload-20261030/workload.py \
      --source artifacts/FAA-7-production/r20261028/export --season 2026 --out <dir>
`--source` is a .duckdb file (opened read_only) or a directory of Parquet tables.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace

import duckdb
import pandas as pd

from fpl_agent.db import eligibility
from fpl_agent.model.features import OBSERVED_SNAPSHOT

CANDIDATE = "minutes-workload-v0"  # feature-set id; a model using it must not be "sim-v1"
FINISH_BUFFER = timedelta(hours=3)  # a match kicked off at least 3 h before C has finished
EUROPE = ("UCL", "UEL", "UECL")
TABLES = ("appearances", "matches", "player_ids", "fpl_players_gw", "fpl_points_gw",
          "fpl_deadlines", "match_odds_log", "repair_log")  # fmt: skip
WINDOW_COLS = ["match_id", "player_id", "minutes", "started", "ingested_at", "kickoff",
               "competition_id", "same_club"]  # fmt: skip


def open_source(src: Path) -> SimpleNamespace:
    """A read-only handle shaped like FootballDB for `eligibility.gameweek`."""
    if src.is_dir():
        con = duckdb.connect()
        present = []
        for t in TABLES:
            p = src / f"{t}.parquet"
            if p.exists():
                con.execute(f"CREATE VIEW {t} AS SELECT * FROM read_parquet('{p.as_posix()}')")
                present.append(t)
        missing = [t for t in TABLES if t not in present]
    else:
        con = duckdb.connect(str(src), read_only=True)
        have = {r[0] for r in con.execute("SELECT table_name FROM duckdb_tables()").fetchall()}
        missing = [t for t in TABLES if t not in have]
    need = {"appearances", "matches", "player_ids", "fpl_players_gw", "fpl_points_gw"}
    if need & set(missing):
        raise SystemExit(f"source lacks required tables: {sorted(need & set(missing))}")
    return SimpleNamespace(con=con, missing_tables=missing)


def population(db, season: int, snapshot_at) -> pd.DataFrame:
    """Players of the one observed pull eligibility uses, with their FotMob link status."""
    return db.con.execute(
        f"SELECT f.fpl_id, f.player_id, f.team_id, f.position, "
        "f.player_id IN (SELECT player_id FROM player_ids WHERE source = 'fotmob') AS linked "
        f"FROM fpl_players_gw f WHERE f.season = ? AND f.snapshot_at = ? "
        f"AND coalesce({OBSERVED_SNAPSHOT}, false)", [season, snapshot_at]
    ).df()  # fmt: skip


def window_rows(db, cutoff, players: pd.DataFrame) -> pd.DataFrame:
    """Appearances of these players whose match kicked off in [C - 30 d, C - FINISH_BUFFER],
    with `same_club` = played for the club the FPL snapshot lists. Rows for another club are
    identity collisions (name-based links merge namesakes) and are excluded by the caller."""
    if players.empty:
        return pd.DataFrame(columns=WINDOW_COLS)
    db.con.register("_pop", players[["player_id", "team_id"]])
    try:
        return db.con.execute(
            "SELECT a.match_id, a.player_id, a.minutes, a.started, a.ingested_at, m.kickoff, "
            "m.competition_id, a.team_id = p.team_id AS same_club "
            "FROM appearances a JOIN matches m USING (match_id) JOIN _pop p USING (player_id) "
            "WHERE m.kickoff IS NOT NULL "
            "AND m.kickoff >= ?::TIMESTAMP - INTERVAL 30 DAY AND m.kickoff <= ?::TIMESTAMP",
            [cutoff, cutoff - FINISH_BUFFER],
        ).df()  # fmt: skip
    finally:
        db.con.unregister("_pop")


def features(rows: pd.DataFrame, cutoff, prefix: str) -> pd.DataFrame:
    """The workload features of one variant from its admissible appearance rows."""
    cols = ["mins_7d", "mins_14d", "starts_7d", "starts_14d", "apps_14d", "euro_mins_14d",
            "rest_days"]  # fmt: skip
    if rows.empty:
        return pd.DataFrame(columns=["player_id"] + [prefix + c for c in cols])
    r = rows[rows.minutes.fillna(0) > 0].copy()
    age = (pd.Timestamp(cutoff) - r.kickoff).dt.total_seconds() / 86400
    d7, d14 = age <= 7, age <= 14
    r = r.assign(
        m7=r.minutes.where(d7, 0), m14=r.minutes.where(d14, 0),
        s7=(r.started.fillna(False) & d7).astype(int),
        s14=(r.started.fillna(False) & d14).astype(int), a14=d14.astype(int),
        e14=r.minutes.where(d14 & r.competition_id.isin(EUROPE), 0), age=age,
    )  # fmt: skip
    g = r.groupby("player_id").agg(
        mins_7d=("m7", "sum"), mins_14d=("m14", "sum"), starts_7d=("s7", "sum"),
        starts_14d=("s14", "sum"), apps_14d=("a14", "sum"), euro_mins_14d=("e14", "sum"),
        rest_days=("age", "min"),
    )  # fmt: skip
    return g.add_prefix(prefix).reset_index()


def repaired_after(db, cutoff) -> set[tuple[int, int]] | None:
    """(match_id, player_id) of appearance rows an automatic repair wrote after C, from the
    FAA-41 journal; None when the source has no `repair_log` (repairs unprovable)."""
    if "repair_log" in db.missing_tables:
        return None
    return {(int(m), int(p)) for m, p in db.con.execute(
        "SELECT json_extract(image, '$.match_id')::INTEGER, "
        "json_extract(image, '$.player_id')::INTEGER FROM repair_log "
        "WHERE table_name = 'appearances' AND action = 'added' AND logged_at > ?", [cutoff]
    ).fetchall()}  # fmt: skip


def gameweek(db, season: int, gw: int) -> tuple[pd.DataFrame, dict]:
    ev = eligibility.gameweek(db, season, gw)
    info = {k: ev[k] for k in ("gw", "class", "cutoff", "cutoff_kind", "snapshot_used")}
    if ev["cutoff"] is None or ev["snapshot_used"] is None:
        info["reason"] = "no cutoff or no observed snapshot by the cutoff: no population"
        return pd.DataFrame(), info
    cutoff = pd.Timestamp(ev["cutoff"]).to_pydatetime()
    pop = population(db, season, pd.Timestamp(ev["snapshot_used"]).to_pydatetime())
    linked = pop[pop.linked]
    raw = window_rows(db, cutoff, linked)
    raw = raw.assign(same_club=raw.same_club.fillna(False).astype(bool))
    obs_raw = raw[raw.ingested_at <= cutoff]
    repaired = repaired_after(db, cutoff)
    n_repaired = 0
    if repaired:
        hit = [(m, p) in repaired for m, p in zip(obs_raw.match_id, obs_raw.player_id, strict=True)]
        n_repaired = sum(hit)
        obs_raw = obs_raw[[not h for h in hit]]
    rows = raw[raw.same_club]
    obs = obs_raw[obs_raw.same_club]
    out = pop.assign(season=season, gw=gw, cutoff=cutoff, candidate=CANDIDATE)
    suspects = {}
    for variant, admissible, prefix in ((obs_raw, obs, "obs_"), (raw, rows, "rec_")):
        # other-club rows and no FPL-club row in 30 days: the link is suspect, so unknown
        suspect = set(variant.player_id[~variant.same_club]) - set(admissible.player_id)
        suspects[prefix] = suspect
        f = features(admissible, cutoff, prefix)
        out = out.merge(f, on="player_id", how="left")
        num = [c for c in f.columns if c != "player_id" and c != prefix + "rest_days"]
        out[prefix + "link_suspect"] = out.player_id.isin(suspect)
        known = out.linked & ~out[prefix + "link_suspect"]
        # linked, trusted players with no admissible appearance played 0 (in covered data)
        out.loc[known, num] = out.loc[known, num].fillna(0)
        out[prefix + "no_recent_30d"] = known & out[prefix + "rest_days"].isna()
    n_rows = len(rows)
    info.update({
        "population": len(pop), "linked": int(pop.linked.sum()),
        "linked_share": round(float(pop.linked.mean()), 4) if len(pop) else None,
        "window_rows_other_club_dropped": len(raw) - n_rows,
        "players_with_other_club_rows": int(raw.loc[~raw.same_club.fillna(False), "player_id"]
                                            .nunique()) if not raw.empty else 0,
        "link_suspect_players_obs": len(suspects["obs_"]),
        "link_suspect_players_rec": len(suspects["rec_"]),
        "repair_proof": "none: source has no repair_log, so in-place repairs after the cutoff "
        "cannot be ruled out" if repaired is None else
        f"repair_log present: {n_repaired} observed rows re-written after the cutoff excluded",
        "window_rows_30d": n_rows,
        "window_rows_observed_by_cutoff": len(obs),
        "observed_row_share": round(len(obs) / n_rows, 4) if n_rows else None,
        "players_with_minutes_14d_rec": int((out["rec_apps_14d"] > 0).sum()),
        "players_with_minutes_14d_obs": int((out["obs_apps_14d"] > 0).sum()),
        "first_ingest_in_window": str(rows.ingested_at.min()) if n_rows else None,
        "usable_for": "eligible evaluation (obs_ only)"
        if ev["class"] == "eligible" and repaired is not None else "exploratory only",
    })  # fmt: skip
    return out, info


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--source", type=Path, required=True)
    ap.add_argument("--season", type=int, required=True)
    ap.add_argument("--gws", type=int, nargs="*")
    ap.add_argument("--out", type=Path, required=True)
    a = ap.parse_args(argv)
    db = open_source(a.source)
    gws = a.gws or [r[0] for r in db.con.execute(
        "SELECT DISTINCT gw FROM fpl_points_gw WHERE season = ? ORDER BY 1", [a.season]
    ).fetchall()]  # fmt: skip
    frames, cov = [], []
    for gw in gws:
        f, info = gameweek(db, a.season, gw)
        frames.append(f)
        cov.append(info)
    a.out.mkdir(parents=True, exist_ok=True)
    feats = pd.concat([f for f in frames if not f.empty], ignore_index=True)
    feats = feats.sort_values(["gw", "fpl_id"]).reset_index(drop=True)
    csv = a.out / f"features-{a.season}.csv"
    feats.to_csv(csv, index=False, lineterminator="\n")
    summary = {
        "candidate": CANDIDATE, "source": a.source.as_posix(), "season": a.season,
        "missing_tables": db.missing_tables, "features_csv": csv.name,
        "features_sha256": hashlib.sha256(csv.read_bytes()).hexdigest(),
        "eligible_gws": [c["gw"] for c in cov if c["class"] == "eligible"],
        "gameweeks": cov,
    }  # fmt: skip
    (a.out / f"coverage-{a.season}.json").write_text(json.dumps(summary, indent=1, default=str))
    print(json.dumps({k: summary[k] for k in ("features_sha256", "eligible_gws")}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
