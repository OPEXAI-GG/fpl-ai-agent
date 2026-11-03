"""A small FPL squad and team list for news tests (ids are canonical player ids)."""

from __future__ import annotations

import json
from datetime import datetime

import pandas as pd

from fpl_agent.db.database import FootballDB

TEAMS = {1: "Arsenal", 2: "Chelsea", 3: "Leeds United", 4: "Everton", 5: "Crystal Palace",
         6: "Manchester City"}  # fmt: skip
SQUAD = [  # player_id, first, second, web, team, position
    (10, "William", "Saliba", "Saliba", 1, "DEF"),
    (11, "Bukayo", "Saka", "Saka", 1, "MID"),
    (20, "Reece", "James", "James", 2, "DEF"),
    (21, "Cole", "Palmer", "Palmer", 2, "MID"),
    (30, "Daniel", "James", "James", 3, "MID"),
    (40, "Jack", "Grealish", "Grealish", 4, "MID"),
    (50, "Jean-Philippe", "Mateta", "Mateta", 5, "FWD"),
    (60, "Erling", "Haaland", "Haaland", 6, "FWD"),
]


def squad_db(db: FootballDB | None = None, season: int = 2026, gw: int = 10) -> FootballDB:
    db = db or FootballDB()
    for tid, name in TEAMS.items():
        db.con.execute("INSERT OR REPLACE INTO teams VALUES (?, ?, ?)",
                       [tid, name, name.lower().replace(" united", "")])  # fmt: skip
    rows = []
    for pid, first, second, web, team, pos in SQUAD:
        db.con.execute("INSERT OR REPLACE INTO players VALUES (?, ?, NULL, ?)",
                       [pid, f"{first} {second}", pos])  # fmt: skip
        rows.append({"season": season, "gw": gw, "snapshot_at": datetime(2026, 11, 1),
                     "fpl_id": pid, "player_id": pid, "team_id": team, "position": pos,
                     "now_cost": 60, "status": "a", "chance_next": None, "news": "",
                     "raw": json.dumps({"first_name": first, "second_name": second,
                                        "web_name": web})})  # fmt: skip
    db.upsert("fpl_players_gw", pd.DataFrame(rows))
    return db
