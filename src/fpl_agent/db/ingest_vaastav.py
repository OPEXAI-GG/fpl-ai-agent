"""vaastav FPL archive -> historical official points and prices (2016/17 onward), the training
history for the models. Season is the start year (2024 = 2024/25).

URL: https://raw.githubusercontent.com/vaastav/Fantasy-Premier-League/master/data/<YYYY-YY>/gws/merged_gw.csv
Players are keyed per season (`vaastav-<season>`, FPL element ids are reassigned every year) and
linked to the canonical player when the full name is unambiguous.
"""

from __future__ import annotations

import io
import json
from datetime import datetime

import pandas as pd

from fpl_agent.data.id_map import normalise
from fpl_agent.db.database import FootballDB, now

POS = {"GKP": "GKP", "GK": "GKP", "DEF": "DEF", "MID": "MID", "FWD": "FWD", "AM": "MID"}
STAT_COLS = ["minutes", "goals_scored", "assists", "clean_sheets", "goals_conceded", "own_goals",
             "penalties_saved", "penalties_missed", "yellow_cards", "red_cards", "saves", "bonus",
             "bps", "defensive_contribution", "starts", "expected_goals", "expected_assists",
             "expected_goals_conceded"]  # fmt: skip
REQUIRED = {"name", "element", "total_points", "minutes", "round", "fixture", "value"}


def url(season: int) -> str:
    label = f"{season}-{(season + 1) % 100:02d}"
    return (
        "https://raw.githubusercontent.com/vaastav/Fantasy-Premier-League/master/data/"
        f"{label}/gws/merged_gw.csv"
    )


def players_raw_url(season: int) -> str:
    return url(season).replace("gws/merged_gw.csv", "players_raw.csv")


ELEMENT_TYPES = {1: "GKP", 2: "DEF", 3: "MID", 4: "FWD"}


def positions_from_players_raw(text: str) -> dict[int, str]:
    """element id -> position, for the seasons whose gameweek file has no position column."""
    df = pd.read_csv(io.StringIO(text), usecols=["id", "element_type"])
    return {int(r.id): ELEMENT_TYPES[int(r.element_type)] for r in df.itertuples()}


def has_position(text: str) -> bool:
    return "position" in pd.read_csv(io.StringIO(text), nrows=1).columns


def problems(text: str) -> list[str]:
    if "<html" in text[:500].lower():
        return ["vaastav: got HTML, not CSV"]
    try:
        cols = set(pd.read_csv(io.StringIO(text), nrows=2).columns)
    except Exception as exc:  # noqa: BLE001
        return [f"vaastav: unreadable CSV ({exc})"]
    missing = REQUIRED - cols
    return [f"vaastav: missing columns {sorted(missing)}"] if missing else []


def _kickoff(value) -> datetime | None:
    if value is None or pd.isna(value):
        return None
    return datetime.fromisoformat(str(value).replace("Z", "")).replace(tzinfo=None)


def load_csv(
    db: FootballDB, text: str, season: int, positions: dict[int, str] | None = None
) -> int:
    df = pd.read_csv(io.StringIO(text.lstrip("﻿")), low_memory=False)
    df["name"] = df["name"].astype(str).str.replace("_", " ")
    if "position" not in df.columns:
        df["position"] = df["element"].map(positions or {})
    index: dict[str, list[int]] = {}
    for pid, name in db.con.execute("SELECT player_id, name FROM players").fetchall():
        index.setdefault(normalise(name), []).append(pid)
    source, stamp = f"vaastav-{season}", now()
    canon: dict[int, int] = {}
    for el, grp in df.groupby("element"):
        name = grp["name"].iloc[0]
        key = normalise(name)
        cands = index.get(key, [])
        link = cands[0] if len(cands) == 1 and len(key.split()) >= 2 else None
        pos = POS.get(str(grp["position"].iloc[0]))
        pid = db.player_id(source, int(el), name, pos, link_to=link)
        canon[int(el)] = pid
        if link is None:
            index.setdefault(key, []).append(pid)
    team_of: dict[str, int] = {}
    if "team" in df.columns:
        for name in df["team"].dropna().unique():
            team_of[str(name)] = db.team_id("vaastav-team", f"{season}:{name}", str(name))
    pts, snaps = [], []
    for r in df.itertuples(index=False):
        row = r._asdict()
        stats = {c: float(row[c]) for c in STAT_COLS if c in row and pd.notna(row[c])}
        stats["total_points"] = float(row["total_points"])
        gw = int(row["round"])
        el = int(row["element"])
        pts.append(
            {"season": season, "gw": gw, "fpl_id": el, "player_id": canon[el],
             "fixture_id": int(row["fixture"]), "minutes": int(row["minutes"]),
             "total_points": int(row["total_points"]),
             "bps": int(row["bps"]) if pd.notna(row.get("bps")) else None,
             "bonus": int(row["bonus"]) if pd.notna(row.get("bonus")) else None,
             "stats": json.dumps(stats), "explain": "[]", "ingested_at": stamp,
             "kickoff": _kickoff(row.get("kickoff_time"))}
        )  # fmt: skip
        if pd.notna(row.get("kickoff_time")):
            ko = datetime.fromisoformat(str(row["kickoff_time"]).replace("Z", "")).replace(
                tzinfo=None
            )
            snaps.append(
                {"season": season, "gw": gw, "snapshot_at": ko, "fpl_id": el,
                 "player_id": canon[el], "position": POS.get(str(row["position"])),
                 "team_id": team_of.get(str(row.get("team"))), "status": "a",
                 "now_cost": int(row["value"]),
                 "raw": json.dumps({"was_home": row.get("was_home")})}
            )  # fmt: skip
    n = db.upsert("fpl_points_gw", pd.DataFrame(pts))
    snap_df = pd.DataFrame(snaps)
    if not snap_df.empty:
        snap_df = snap_df.sort_values("snapshot_at").drop_duplicates(
            ["season", "gw", "fpl_id"], keep="first"
        )  # price at the first kickoff of the GW
        db.upsert("fpl_players_gw", snap_df)
    db.log("vaastav", "fpl_points_gw", n, str(season))
    return n
