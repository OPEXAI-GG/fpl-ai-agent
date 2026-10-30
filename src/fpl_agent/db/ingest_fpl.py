"""FPL -> football database writers."""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

import pandas as pd

from fpl_agent.data.players import POSITIONS
from fpl_agent.db.database import FootballDB, now
from fpl_agent.db.gameweeks import log_deadlines

COMPETITION = "ENG1"


def _parse_ts(s: str | None) -> datetime | None:
    return datetime.fromisoformat(s.replace("Z", "+00:00")).replace(tzinfo=None) if s else None


def ensure_competitions(db: FootballDB) -> None:
    rows = [
        ("ENG1", "Premier League", "England", 1),
        ("ENG2", "Championship", "England", 2),
        ("ENG3", "League One", "England", 3),
    ]
    for r in rows:
        db.con.execute("INSERT OR REPLACE INTO competitions VALUES (?, ?, ?, ?)", list(r))


def load_bootstrap(
    db: FootballDB,
    raw: dict[str, Any],
    season: int,
    gw: int,
    snapshot_at: datetime | None = None,
) -> int:
    """Teams, players and one fpl_players_gw row per player for this snapshot."""
    ensure_competitions(db)
    snapshot_at = snapshot_at or now()
    team_ids = {t["id"]: db.team_id("fpl", t["id"], t["name"]) for t in raw["teams"]}
    rows = []
    for e in raw["elements"]:
        name = f"{e['first_name']} {e['second_name']}".strip()
        pid = db.player_id("fpl", e["id"], name, POSITIONS.get(e["element_type"]))
        rows.append(
            {
                "season": season,
                "gw": gw,
                "snapshot_at": snapshot_at,
                "fpl_id": e["id"],
                "player_id": pid,
                "team_id": team_ids[e["team"]],
                "position": POSITIONS.get(e["element_type"]),
                "now_cost": e["now_cost"],
                "selected_by_percent": float(e["selected_by_percent"]),
                "status": e["status"],
                "chance_next": e.get("chance_of_playing_next_round"),
                "news": e.get("news") or "",
                "transfers_in_event": e.get("transfers_in_event"),
                "transfers_out_event": e.get("transfers_out_event"),
                "total_points": e["total_points"],
                "minutes": e["minutes"],
                "raw": json.dumps(e),  # every field FPL gave, nothing dropped
            }
        )
    n = db.upsert("fpl_players_gw", pd.DataFrame(rows))
    log_deadlines(db, raw.get("events") or [], season, snapshot_at)
    db.log("fpl", "fpl_players_gw", n, f"gw{gw}")
    return n


def load_fixtures(db: FootballDB, fixtures: list[dict[str, Any]], raw: dict[str, Any], season: int):
    ensure_competitions(db)
    names = {t["id"]: t["name"] for t in raw["teams"]}
    count = 0
    for f in fixtures:
        if not f.get("kickoff_time"):
            continue
        ko = _parse_ts(f["kickoff_time"])
        h = db.team_id("fpl", f["team_h"], names[f["team_h"]])
        a = db.team_id("fpl", f["team_a"], names[f["team_a"]])
        db.match_id(
            "fpl",
            f["id"],
            COMPETITION,
            season,
            ko.date(),
            h,
            a,
            matchday=f.get("event"),
            kickoff=ko,
            home_goals=f.get("team_h_score"),
            away_goals=f.get("team_a_score"),
            finished=bool(f.get("finished")),
        )
        count += 1
    db.log("fpl", "matches", count)
    return count


def load_live(db: FootballDB, live: dict[str, Any], season: int, gw: int) -> int:
    """Official points per player per fixture, with FPL's own explain breakdown."""
    rows = []
    kickoffs = dict(
        db.con.execute(
            "SELECT i.source_id::INTEGER, m.kickoff FROM match_ids i JOIN matches m USING "
            "(match_id) WHERE i.source = 'fpl'"
        ).fetchall()
    )
    for el in live["elements"]:
        row = db.con.execute(
            "SELECT player_id FROM player_ids WHERE source = 'fpl' AND source_id = ?",
            [str(el["id"])],
        ).fetchone()
        pid = row[0] if row else None
        stats = el["stats"]
        explain = {x["fixture"]: x["stats"] for x in el.get("explain", [])}
        fixtures = list(explain) or [0]
        for fx in fixtures:
            rows.append(
                {
                    "season": season,
                    "gw": gw,
                    "fpl_id": el["id"],
                    "player_id": pid,
                    "fixture_id": fx,
                    "minutes": stats.get("minutes"),
                    "total_points": stats.get("total_points")
                    if len(fixtures) == 1
                    else sum(s["points"] for s in explain[fx]),
                    "bps": stats.get("bps"),
                    "bonus": stats.get("bonus"),
                    "stats": json.dumps(stats),
                    "explain": json.dumps(explain.get(fx, [])),
                    "ingested_at": now(),
                    "kickoff": kickoffs.get(fx),
                }
            )
    n = db.upsert("fpl_points_gw", pd.DataFrame(rows))
    db.log("fpl", "fpl_points_gw", n, f"gw{gw}")
    return n
