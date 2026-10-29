"""FotMob -> matches, team xG, lineups/appearances, per-player stats and shots.

League pages give fixtures and results; match pages give everything else. Defensive-contribution
proxies (CBIT / CBIRT, the quantities FPL's DefCon thresholds use) are computed for every player
in every league, which is what lets a new signing from abroad have a DefCon estimate on day one.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime
from typing import Any

import pandas as pd

from fpl_agent.data.id_map import normalise
from fpl_agent.data.leagues import League
from fpl_agent.db.database import FootballDB, now

POSITIONS = {0: "GKP", 1: "DEF", 2: "MID", 3: "FWD"}
SOURCE = "fotmob"

# FotMob stat key -> our stat name
PLAYER_STATS = {
    "goals": "goals",
    "assists": "assists",
    "expected_goals": "xG",
    "expected_assists": "xA",
    "expected_goals_non_penalty": "npxG",
    "expected_goals_on_target_variant": "xGOT",
    "total_shots": "shots",
    "ShotsOnTarget": "shots_on_target",
    "chances_created": "key_passes",
    "touches": "touches",
    "touches_opp_box": "touches_opp_box",
    "defensive_actions": "defensive_actions",
    "matchstats.headers.tackles": "tackles",
    "interceptions": "interceptions",
    "clearances": "clearances",
    "shot_blocks": "blocks",
    "recoveries": "recoveries",
    "fouls": "fouls",
    "was_fouled": "was_fouled",
    "saves": "saves",
    "goals_conceded": "goals_conceded",
    "expected_goals_on_target_faced": "xGOT_faced",
    "goals_prevented": "goals_prevented",
    "rating_title": "rating",
}
TEAM_STATS = {
    "expected_goals": "xg",
    "total_shots": "shots",
    "ShotsOnTarget": "shots_on_target",
    "corners": "corners",
    "fouls": "fouls",
    "yellow_cards": "yellows",
    "red_cards": "reds",
}


def register_competitions(db: FootballDB, leagues: list[League]) -> None:
    for lg in leagues:
        db.con.execute(
            "INSERT OR REPLACE INTO competitions VALUES (?, ?, ?, ?)",
            [lg.comp_id, lg.name, lg.country, lg.tier],
        )


def _ts(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00")).replace(tzinfo=None)


class FotMobIngestor:
    """Holds a name index of canonical players so a FotMob player who is also an FPL player (or
    seen in another source) is linked to the same canonical id instead of duplicated."""

    def __init__(self, db: FootballDB) -> None:
        self.db = db
        self._by_name: dict[str, list[int]] = defaultdict(list)
        for pid, name in db.con.execute("SELECT player_id, name FROM players").fetchall():
            self._by_name[normalise(name)].append(pid)

    def player(self, fm_id: int | str, name: str, position: str | None) -> int:
        existing = self.db.con.execute(
            "SELECT player_id FROM player_ids WHERE source = ? AND source_id = ?",
            [SOURCE, str(fm_id)],
        ).fetchone()
        if existing:
            return existing[0]
        key = normalise(name)
        # a wrong link silently mixes two people's records; an unlinked player is merely a
        # second row: see FootballDB.name_link for the conditions
        link = self.db.name_link(self._by_name.get(key, []), name, position, SOURCE)
        pid = self.db.player_id(SOURCE, fm_id, name, position, link_to=link)
        if link is None:
            self._by_name[key].append(pid)
        return pid

    # ---- league page: fixtures and results ---------------------------------------------
    def load_league(self, raw: dict[str, Any], league: League, season: int) -> int:
        register_competitions(self.db, [league])
        n = 0
        for m in raw["fixtures"]["allMatches"]:
            status = m["status"]
            rnd = str(m.get("round", ""))
            if status.get("cancelled") or (not league.cup and not rnd.isdigit()):
                continue  # promotion/relegation play-offs repeat league pairings: kept out
            ko = _ts(status["utcTime"])
            h = self.db.team_id(SOURCE, m["home"]["id"], m["home"]["name"])
            a = self.db.team_id(SOURCE, m["away"]["id"], m["away"]["name"])
            finished = bool(status.get("finished"))
            hg = ag = None
            if finished and " - " in status.get("scoreStr", ""):
                hg, ag = (int(x) for x in status["scoreStr"].split(" - "))
            self.db.match_id(
                SOURCE, m["id"], league.comp_id, season, ko.date(), h, a,
                allow_repeat=league.cup or league.repeats,
                matchday=int(rnd) if rnd.isdigit() and not league.cup else None,
                kickoff=ko, home_goals=hg, away_goals=ag, finished=finished,
            )  # fmt: skip
            n += 1
        self.db.log(SOURCE, "matches", n, f"{league.comp_id} {season}")
        return n

    # ---- match page ----------------------------------------------------------------------
    def load_match(self, raw: dict[str, Any], league: League, season: int) -> int:
        """Store one finished match. Returns the number of appearance rows written."""
        gen = raw["general"]
        if not gen.get("finished"):
            return 0
        db, stamp = self.db, now()
        h = db.team_id(SOURCE, gen["homeTeam"]["id"], gen["homeTeam"]["name"])
        a = db.team_id(SOURCE, gen["awayTeam"]["id"], gen["awayTeam"]["name"])
        ko = _ts(gen["matchTimeUTCDate"].replace(".000Z", "Z"))
        score = raw["header"]["teams"]
        hg, ag = int(score[0]["score"]), int(score[1]["score"])
        mid = db.match_id(
            SOURCE, gen["matchId"], league.comp_id, season, ko.date(), h, a,
            allow_repeat=league.cup or league.repeats, kickoff=ko, home_goals=hg, away_goals=ag,
            finished=True,
        )  # fmt: skip
        self._team_rows(raw, mid, h, a, hg, ag, stamp)
        length = _match_length(raw)
        apps, stats, cards = [], [], {}
        content = raw["content"]
        pstats = content["playerStats"] or {}
        for side, team in (("homeTeam", h), ("awayTeam", a)):
            lineup = content["lineup"][side]
            for group, started in (("starters", True), ("subs", False)):
                for p in lineup.get(group) or []:
                    pos = POSITIONS.get(p.get("usualPlayingPositionId"))
                    pid = self.player(p["id"], p["name"], pos)
                    s = _flat(pstats.get(str(p["id"])))
                    minutes = _minutes(p, s, started, length)
                    apps.append(
                        {"match_id": mid, "player_id": pid, "team_id": team, "source": SOURCE,
                         "started": started, "minutes": minutes, "position": pos,
                         "ingested_at": stamp}
                    )  # fmt: skip
                    perf = p.get("performance") or {}
                    ev = [e["type"] for e in perf.get("events") or []]
                    cards[pid] = (ev.count("yellowCard"), ev.count("redCard"))
                    if minutes > 0 or s:
                        stats += _stat_rows(mid, pid, s, pos, cards[pid], stamp)
        db.upsert("appearances", pd.DataFrame(apps))
        db.upsert("player_match_stats", pd.DataFrame(stats))
        self._shots(raw, mid, stamp)
        db.log(SOURCE, "match", len(apps), f"{league.comp_id} {gen['matchId']}")
        return len(apps)

    def _team_rows(self, raw, mid, h, a, hg, ag, stamp) -> None:
        flat: dict[str, list] = {}
        periods = (raw["content"]["stats"] or {}).get("Periods", {}).get("All", {})
        for group in periods.get("stats", []):
            for s in group.get("stats", []):
                flat[s["key"]] = [(r or {}).get("value") for r in s.get("rawStats", [])]
        rows = []
        for i, (team, home, gf, ga) in enumerate(((h, True, hg, ag), (a, False, ag, hg))):
            row = {"match_id": mid, "team_id": team, "source": SOURCE, "is_home": home,
                   "goals_for": gf, "goals_against": ga, "ingested_at": stamp}  # fmt: skip
            for key, col in TEAM_STATS.items():
                vals = flat.get(key)
                row[col] = (
                    float(vals[i]) if vals and len(vals) == 2 and vals[i] is not None else None
                )
            opp = flat.get("expected_goals")
            row["xg_for"] = row.pop("xg")
            row["xg_against"] = float(opp[1 - i]) if opp and len(opp) == 2 else None
            for c in ("shots", "shots_on_target", "corners", "fouls", "yellows", "reds"):
                row[c] = None if row[c] is None else int(row[c])
            rows.append(row)
        self.db.upsert("team_match", pd.DataFrame(rows))

    def _shots(self, raw, mid, stamp) -> None:
        rows = []
        for s in (raw["content"]["shotmap"] or {}).get("shots") or []:
            pid = None
            if s.get("playerId"):
                pid = self.player(s["playerId"], s.get("fullName") or s.get("playerName", ""), None)
            rows.append(
                {"source": SOURCE, "shot_id": str(s["id"]),
                 "source_match_id": str(raw["general"]["matchId"]), "match_id": mid,
                 "player_id": pid, "minute": int(s.get("min") or 0),
                 "xg": s.get("expectedGoals"), "result": s.get("eventType"),
                 "situation": s.get("situation"), "shot_type": s.get("shotType"),
                 "x": _unit(s.get("x"), 105.0), "y": _unit(s.get("y"), 68.0),
                 "assisted_by": None, "last_action": None,
                 "ingested_at": stamp}
            )  # fmt: skip
        self.db.upsert("shots", pd.DataFrame(rows))


def _unit(v: float | None, size: float) -> float | None:
    """FotMob pitch coordinates (105 x 68) -> the 0-1 scale Understat uses."""
    return None if v is None else min(max(float(v) / size, 0.0), 1.0)


def _match_length(raw: dict[str, Any]) -> int:
    """Minutes played including stoppage time, from the half timestamps; 90 if unavailable."""
    halfs = (raw["header"]["status"] or {}).get("halfs") or {}
    try:
        fmt = "%d.%m.%Y %H:%M:%S"
        start = datetime.strptime(halfs["firstHalfStarted"], fmt)
        end = datetime.strptime(halfs["gameEnded"], fmt)
        total = (end - start).total_seconds() / 60 - 15  # remove the half-time break
        return int(min(max(round(total), 90), 125))
    except (KeyError, ValueError, TypeError):
        return 90


def _flat(entry: dict[str, Any] | None) -> dict[str, float]:
    """One player's per-match stats as {fotmob key: value}."""
    out: dict[str, float] = {}
    for section in (entry or {}).get("stats", []):
        for item in section.get("stats", {}).values():
            key, stat = item.get("key"), item.get("stat") or {}
            if key and stat.get("value") is not None and key not in out:
                out[key] = float(stat["value"])
    return out


def _minutes(p: dict[str, Any], s: dict[str, float], started: bool, length: int) -> int:
    if "minutes_played" in s:
        return int(s["minutes_played"])
    subs = {
        e["type"]: e["time"] for e in (p.get("performance") or {}).get("substitutionEvents", [])
    }
    if started:
        return int(subs.get("subOut", length))
    return max(0, length - int(subs["subIn"])) if "subIn" in subs else 0


def _stat_rows(mid, pid, s, pos, cards, stamp) -> list[dict[str, Any]]:
    vals = {name: s[key] for key, name in PLAYER_STATS.items() if key in s}
    defensive = ("clearances", "blocks", "interceptions", "tackles")
    # Missing provider fields are unknown, not zero defensive actions.
    if all(k in vals for k in defensive):
        vals["cbit"] = sum(vals[k] for k in defensive)
        if "recoveries" in vals:
            vals["cbirt"] = vals["cbit"] + vals["recoveries"]
    vals["yellow_cards"], vals["red_cards"] = float(cards[0]), float(cards[1])
    return [
        {"match_id": mid, "player_id": pid, "source": SOURCE, "stat": k, "value": float(v),
         "ingested_at": stamp}
        for k, v in vals.items()
    ]  # fmt: skip
