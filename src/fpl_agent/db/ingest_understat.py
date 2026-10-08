"""Understat -> matches, team xG, per-player match stats, appearances and shots."""

from __future__ import annotations

from datetime import datetime
from typing import Any

import pandas as pd

from fpl_agent.db.database import FootballDB, now

COMPETITIONS = {"EPL": "ENG1"}
STATS = ["goals", "assists", "shots", "xG", "xA", "key_passes", "npg", "npxG", "xGChain",
         "xGBuildup"]  # fmt: skip


def load_league(
    db: FootballDB, league_raw: dict[str, Any], season: int, league: str = "EPL"
) -> int:
    """Fixtures/results with match xG from `dates`; creates canonical matches and team_match."""
    comp = COMPETITIONS[league]
    stamp = now()
    rows, n = [], 0
    for m in league_raw["dates"]:
        ko = datetime.strptime(m["datetime"], "%Y-%m-%d %H:%M:%S")
        h = db.team_id("understat", m["h"]["id"], m["h"]["title"])
        a = db.team_id("understat", m["a"]["id"], m["a"]["title"])
        res = bool(m["isResult"])
        hg = int(m["goals"]["h"]) if res else None
        ag = int(m["goals"]["a"]) if res else None
        mid = db.match_id(
            "understat", m["id"], comp, season, ko.date(), h, a,
            kickoff=ko, home_goals=hg, away_goals=ag, finished=res,
        )  # fmt: skip
        if res:
            hx, ax = float(m["xG"]["h"]), float(m["xG"]["a"])
            for team, home, gf, ga, xf, xa in (
                (h, True, hg, ag, hx, ax),
                (a, False, ag, hg, ax, hx),
            ):
                rows.append(
                    {"match_id": mid, "team_id": team, "source": "understat", "is_home": home,
                     "goals_for": gf, "goals_against": ga, "xg_for": xf, "xg_against": xa,
                     "ingested_at": stamp}
                )  # fmt: skip
        n += 1
    db.upsert("team_match", pd.DataFrame(rows))
    db.log("understat", "matches", n, f"{league} {season}")
    return n


def load_player(db: FootballDB, data: dict[str, Any], fpl_player_id: int | None = None) -> int:
    """One player's full history. `fpl_player_id` links the Understat id to the canonical player."""
    stamp = now()
    uid = str(data["player"]["id"])
    pid = db.player_id(
        "understat", uid, data["player"]["name"], data["player"].get("favorite_position"),
        link_to=fpl_player_id,
    )  # fmt: skip
    app_rows, stat_rows = [], []
    season_clubs = _season_clubs(data)
    for m in data["matches"]:
        row = db.con.execute(
            "SELECT match_id FROM match_ids WHERE source = 'understat' AND source_id = ?",
            [str(m["id"])],
        ).fetchone()
        if not row:
            continue  # other league/season: filled in by the league backfill
        mid = row[0]
        side_team = _player_team(db, mid, m, season_clubs)
        app_rows.append(
            {"match_id": mid, "player_id": pid, "team_id": side_team, "source": "understat",
             "started": None, "minutes": int(m["time"]), "position": m["position"],
             "ingested_at": stamp}
        )  # fmt: skip
        for s in STATS:
            if m.get(s) is not None:
                stat_rows.append(
                    {"match_id": mid, "player_id": pid, "source": "understat", "stat": s,
                     "value": float(m[s]), "ingested_at": stamp}
                )  # fmt: skip
    shot_rows = []
    for s in data.get("shots", []):
        row = db.con.execute(
            "SELECT match_id FROM match_ids WHERE source = 'understat' AND source_id = ?",
            [str(s["match_id"])],
        ).fetchone()
        shot_rows.append(
            {"source": "understat", "shot_id": str(s["id"]), "source_match_id": str(s["match_id"]),
             "match_id": row[0] if row else None, "player_id": pid, "minute": int(s["minute"]),
             "xg": float(s["xG"]), "result": s["result"], "situation": s["situation"],
             "shot_type": s["shotType"], "x": float(s["X"]), "y": float(s["Y"]),
             "assisted_by": s.get("player_assisted"), "last_action": s.get("lastAction"),
             "ingested_at": stamp}
        )  # fmt: skip
    db.upsert("appearances", pd.DataFrame(app_rows))
    db.upsert("player_match_stats", pd.DataFrame(stat_rows))
    db.upsert("shots", pd.DataFrame(shot_rows))
    db.log("understat", "player", len(app_rows) + len(shot_rows), uid)
    return len(app_rows)


def _season_clubs(data: dict[str, Any]) -> dict[str, set[str]]:
    """season -> club names the player was listed at (Understat joins several with commas)."""
    return {
        str(g["season"]): {c.strip() for c in g["team"].split(",")}
        for g in data.get("groups", {}).get("season", [])
    }


def _player_team(
    db: FootballDB, mid: int, m: dict[str, Any], season_clubs: dict[str, set[str]]
) -> int | None:
    """Understat rows name both clubs but not the player's side. If exactly one of them is a club
    the player was listed at that season, that is his team; otherwise leave it NULL (FotMob
    lineups fill it later) rather than guess."""
    clubs = season_clubs.get(str(m.get("season")), set())
    sides = [t for t in (m["h_team"], m["a_team"]) if t in clubs]
    if len(sides) != 1:
        return None
    row = db.con.execute(
        "SELECT team_id FROM team_ids WHERE source = 'understat' AND source_name = ?", [sides[0]]
    ).fetchone()
    return row[0] if row else None
