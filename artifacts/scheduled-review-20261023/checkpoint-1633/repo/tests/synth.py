"""A synthetic football world with known truth, written into a FootballDB.

Models are tested by recovering the parameters that generated the data (team strengths, start
probabilities, goal shares), which real data cannot offer. Everything is seeded.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

import numpy as np
import pandas as pd

from fpl_agent.data.players import POSITIONS
from fpl_agent.db.database import FootballDB, now
from fpl_agent.rules import Rules, score

SQUAD = ["GKP"] * 2 + ["DEF"] * 6 + ["MID"] * 6 + ["FWD"] * 3
GOAL_W = {"GKP": 0.0, "DEF": 0.35, "MID": 1.0, "FWD": 2.2}
CBIT_MU = {"GKP": 1.0, "DEF": 7.0, "MID": 8.0, "FWD": 5.0}  # per 90, FPL definition
ASSIST_W = {"GKP": 0.0, "DEF": 0.45, "MID": 1.0, "FWD": 0.9}


@dataclass
class World:
    db: FootballDB
    seed: int
    teams: dict[int, dict]  # team_id -> {att, dfn}
    players: dict[int, dict]  # player_id -> truth
    seasons: list[int]
    mu: float = 0.15
    home: float = 0.25
    fixtures: list[tuple[int, int, int, date]] = field(default_factory=list)  # season, h, a, date


def build_world(
    n_teams: int = 8,
    seasons: tuple[int, ...] = (2024, 2025),
    seed: int = 7,
    comp: str = "ENG1",
    db: FootballDB | None = None,
    players: bool = True,
    xg_noise: float = 0.15,
) -> World:
    rng = np.random.default_rng(seed)
    db = db or FootballDB()
    db.con.execute(
        "INSERT OR REPLACE INTO competitions VALUES (?, ?, ?, ?)", [comp, comp, "Synthland", 1]
    )
    teams = {}
    for i in range(n_teams):
        tid = db.team_id("synth", i, f"Team {chr(65 + i)}")
        teams[tid] = {"att": float(rng.normal(0, 0.28)), "dfn": float(rng.normal(0, 0.28))}
    world = World(db, seed, teams, {}, list(seasons))
    ids = list(teams)
    pid = 0
    squads: dict[int, list[int]] = {}
    if players:
        for tid in ids:
            squads[tid] = []
            for k, pos in enumerate(SQUAD):
                pid += 1
                p = db.player_id("synth", pid, f"Player {pid} Synth", pos)
                first_choice = k in (0, 2, 3, 4, 5, 8, 9, 10, 11, 14, 15) or rng.random() < 0.2
                start_p = float(rng.uniform(0.82, 0.97) if first_choice else rng.uniform(0.02, 0.3))
                world.players[p] = {
                    "team": tid, "pos": pos, "start_p": start_p, "p60": float(rng.uniform(0.7, 0.92)),
                    "goal_w": GOAL_W[pos] * float(rng.uniform(0.6, 1.4)) * (1 if first_choice else 0.5),
                    "assist_w": ASSIST_W[pos] * float(rng.uniform(0.6, 1.4)),
                    "cbit_mu": CBIT_MU[pos] * float(rng.uniform(0.6, 1.5)),
                    "yellow_p": float(rng.uniform(0.03, 0.18)),
                }  # fmt: skip
                squads[tid].append(p)
    rules = Rules.load(2026)
    stamp = now()
    for si, season in enumerate(seasons):
        start = date(season, 8, 10)
        rounds = []
        order = ids[:]
        for r in range(len(ids) - 1):
            half = len(order) // 2
            rounds.append(list(zip(order[:half], order[::-1][:half], strict=True)))
            order = [order[0], order[-1], *order[1:-1]]
        rounds = rounds + [[(a, h) for h, a in rnd] for rnd in rounds]
        for gw, rnd in enumerate(rounds, start=1):
            day = start + timedelta(days=7 * (gw - 1))
            for h, a in rnd:
                world.fixtures.append((season, h, a, day))
                lh = np.exp(world.mu + world.home + teams[h]["att"] + teams[a]["dfn"])
                la = np.exp(world.mu + teams[a]["att"] + teams[h]["dfn"])
                hg, ag = int(rng.poisson(lh)), int(rng.poisson(la))
                hx = float(lh * np.exp(rng.normal(0, xg_noise)))
                ax = float(la * np.exp(rng.normal(0, xg_noise)))
                mid = db.match_id(
                    "fotmob", f"{season}-{h}-{a}", comp, season, day, h, a, matchday=gw,
                    kickoff=datetime.combine(day, datetime.min.time()) + timedelta(hours=15),
                    home_goals=hg, away_goals=ag, finished=True,
                )  # fmt: skip
                rows = [
                    {"match_id": mid, "team_id": h, "source": "fotmob", "is_home": True,
                     "goals_for": hg, "goals_against": ag, "xg_for": hx, "xg_against": ax,
                     "ingested_at": stamp},
                    {"match_id": mid, "team_id": a, "source": "fotmob", "is_home": False,
                     "goals_for": ag, "goals_against": hg, "xg_for": ax, "xg_against": hx,
                     "ingested_at": stamp},
                ]  # fmt: skip
                db.upsert("team_match", pd.DataFrame(rows))
                if players:
                    _play(db, world, rng, rules, season, gw, mid, h, a, hg, ag, squads, stamp,
                          (hx, ax))  # fmt: skip
    return world


def _play(db, world, rng, rules, season, gw, mid, h, a, hg, ag, squads, stamp, xgs):
    apps, pts, snaps, pstats = [], [], [], []
    for side, (team, opp_goals, goals) in enumerate(((h, ag, hg), (a, hg, ag))):
        team_xg = xgs[side]
        squad = squads[team]
        mins = {}
        for p in squad:
            t = world.players[p]
            starts = rng.random() < t["start_p"]
            if starts:
                mins[p] = 90 if rng.random() < t["p60"] else int(rng.integers(55, 80))
            else:
                mins[p] = int(rng.integers(8, 30)) if rng.random() < 0.25 else 0
        w = np.array([world.players[p]["goal_w"] * mins[p] / 90 for p in squad])
        aw = np.array([world.players[p]["assist_w"] * mins[p] / 90 for p in squad])
        scored = {p: 0 for p in squad}
        assisted = {p: 0 for p in squad}
        for _ in range(goals):
            if w.sum() > 0:
                scored[squad[rng.choice(len(squad), p=w / w.sum())]] += 1
                if rng.random() < 0.75 and aw.sum() > 0:
                    assisted[squad[rng.choice(len(squad), p=aw / aw.sum())]] += 1
        for p in squad:
            t = world.players[p]
            m = mins[p]
            xg_i = float(team_xg * w[squad.index(p)] / w.sum()) if w.sum() > 0 else 0.0
            xa_i = float(team_xg * 0.75 * aw[squad.index(p)] / aw.sum()) if aw.sum() > 0 else 0.0
            cbit = int(rng.poisson(t["cbit_mu"] * m / 90)) if m > 0 else 0
            yellow = int(rng.random() < t["yellow_p"] * m / 90)
            apps.append({"match_id": mid, "player_id": p, "team_id": team, "source": "fotmob",
                         "started": m >= 55, "minutes": m, "position": t["pos"],
                         "ingested_at": stamp})  # fmt: skip
            stats = {"minutes": m, "goals_scored": scored[p], "assists": assisted[p],
                     "clean_sheets": int(opp_goals == 0 and m >= 60), "goals_conceded": opp_goals,
                     "own_goals": 0, "penalties_saved": 0, "penalties_missed": 0,
                     "yellow_cards": yellow, "red_cards": 0,
                     "saves": int(rng.poisson(2.5)) if t["pos"] == "GKP" and m > 0 else 0,
                     "bonus": 0, "bps": 20 if m > 0 else 0, "defensive_contribution": 0}  # fmt: skip
            if m > 0:
                for stat, val in (
                    ("xG", xg_i),
                    ("xA", xa_i),
                    ("cbit", cbit),
                    ("cbirt", cbit + 1),
                    ("goals", scored[p]),
                    ("assists", assisted[p]),
                    ("yellow_cards", yellow),
                    ("red_cards", 0),
                ):
                    pstats.append(
                        {
                            "match_id": mid,
                            "player_id": p,
                            "source": "fotmob",
                            "stat": stat,
                            "value": float(val),
                            "ingested_at": stamp,
                        }
                    )
            stats.update(
                {
                    "expected_goals": xg_i,
                    "expected_assists": xa_i,
                    "defensive_contribution": cbit if t["pos"] == "DEF" else cbit + 1,
                    "starts": int(m >= 55),
                }
            )
            total = score(stats, t["pos"], rules)["total"]
            stats["total_points"] = total
            pts.append({"season": season, "gw": gw, "fpl_id": p, "player_id": p,
                        "fixture_id": mid, "minutes": m, "total_points": total, "bps": stats["bps"],
                        "bonus": 0, "stats": json.dumps(stats), "explain": "[]",
                        "ingested_at": stamp, "kickoff": datetime.combine(
                            date(season, 8, 10) + timedelta(days=7 * (gw - 1)),
                            datetime.min.time()) + timedelta(hours=15)})  # fmt: skip
            snaps.append({"season": season, "gw": gw, "snapshot_at": datetime.combine(
                date(season, 8, 10) + timedelta(days=7 * (gw - 1)), datetime.min.time()),
                "fpl_id": p, "player_id": p, "team_id": team, "position": t["pos"],
                "now_cost": 45 + int(t["goal_w"] * 20) + (10 if t["pos"] == "FWD" else 0),
                "status": "a", "chance_next": 100, "raw": "{}"})  # fmt: skip
    db.upsert("appearances", pd.DataFrame(apps))
    db.upsert("player_match_stats", pd.DataFrame(pstats))
    db.upsert("fpl_points_gw", pd.DataFrame(pts))
    db.upsert("fpl_players_gw", pd.DataFrame(snaps))


POSITION_CODES = POSITIONS
