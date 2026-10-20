"""FPL -> football database writers."""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

import pandas as pd

from fpl_agent.data.players import POSITIONS
from fpl_agent.db.database import FootballDB, now

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


def _number(v: Any) -> float | None:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def split_stats(
    stats: dict[str, Any], explain: dict[int, list[dict[str, Any]]], fixtures: list[int]
) -> tuple[dict[int, dict[str, Any]], int]:
    """FPL's gameweek totals split per fixture, without guessing.

    The live feed gives one total per gameweek; the explain block gives some quantities per
    fixture. A fixture's value is the explain value; a fixture without one is 0 only when the
    known values already add up to the total. Anything else (xG, starts, a total the explain
    doesn't account for) is None: unknown, never the total copied or divided. Returns the
    per-fixture stats and how many values had to stay unknown."""
    if len(fixtures) == 1:
        return {fixtures[0]: dict(stats)}, 0
    out: dict[int, dict[str, Any]] = {fx: {} for fx in fixtures}
    unknown = 0
    for key, total in stats.items():
        if key == "total_points":
            continue
        v = _number(total)
        known = {
            fx: _number(x.get("value"))
            for fx in fixtures
            for x in explain.get(fx, [])
            if x.get("identifier") == key
        }
        if v is not None and not known and v == 0:
            per = dict.fromkeys(fixtures, 0)
        elif v is not None and None not in known.values() and sum(known.values()) == v:
            per = {fx: known.get(fx, 0) for fx in fixtures}
        else:
            per = dict.fromkeys(fixtures)
            unknown += 1
        for fx in fixtures:
            val = per[fx]
            out[fx][key] = int(val) if isinstance(val, float) and val.is_integer() else val
    for fx in fixtures:
        out[fx]["total_points"] = sum(_number(x.get("points")) or 0 for x in explain.get(fx, []))
    return out, unknown


def load_live(db: FootballDB, live: dict[str, Any], season: int, gw: int) -> int:
    """Official points per player per fixture, with FPL's own explain breakdown.

    A player gets one row per fixture his team played that gameweek (from the explain block and
    the stored fixtures), so a double gameweek's totals are split (see split_stats) and a match he
    sat out is a real zero-minute row. Only a player whose team had no fixture gets the
    placeholder fixture 0, which is not a match and is kept out of training. Reloading a
    gameweek replaces its rows."""
    rows = []
    kickoffs = dict(
        db.con.execute(
            "SELECT i.source_id::INTEGER, m.kickoff FROM match_ids i JOIN matches m USING "
            "(match_id) WHERE i.source = 'fpl'"
        ).fetchall()
    )
    team_fixtures: dict[int, list[int]] = {}
    for fx, home, away in db.con.execute(
        "SELECT i.source_id::INTEGER, m.home_team_id, m.away_team_id FROM match_ids i "
        "JOIN matches m USING (match_id) WHERE i.source = 'fpl' AND m.season = ? "
        "AND m.matchday = ?",
        [season, gw],
    ).fetchall():
        team_fixtures.setdefault(home, []).append(fx)
        team_fixtures.setdefault(away, []).append(fx)
    team_of = dict(
        db.con.execute(
            "SELECT fpl_id, team_id FROM fpl_players_gw WHERE season = ? QUALIFY row_number() "
            "OVER (PARTITION BY fpl_id ORDER BY (gw <= ?) DESC, gw DESC, snapshot_at DESC) = 1",
            [season, gw],
        ).fetchall()
    )
    pids = dict(
        db.con.execute(
            "SELECT source_id::INTEGER, player_id FROM player_ids WHERE source = 'fpl'"
        ).fetchall()
    )
    unknown = placeholders = 0
    for el in live["elements"]:
        stats = el["stats"]
        explain = {x["fixture"]: x["stats"] for x in el.get("explain", [])}
        team = set(team_fixtures.get(team_of.get(el["id"]), []))
        # the stored team is only trusted when it agrees with the explain block (a player who
        # moved club, or a fixture not stored yet, keeps just the fixtures FPL named)
        fixtures = sorted(set(explain) | team if set(explain) <= team else set(explain))
        if not fixtures:
            fixtures = [0]
            placeholders += 1
        per, n_unknown = split_stats(stats, explain, fixtures)
        unknown += n_unknown
        for fx in fixtures:
            fs = per[fx]
            rows.append(
                {
                    "season": season,
                    "gw": gw,
                    "fpl_id": el["id"],
                    "player_id": pids.get(el["id"]),
                    "fixture_id": fx,
                    "minutes": fs.get("minutes"),
                    "total_points": stats.get("total_points")
                    if len(fixtures) == 1
                    else fs["total_points"],  # fmt: skip
                    "bps": fs.get("bps"),
                    "bonus": fs.get("bonus"),
                    "stats": json.dumps(fs),
                    "explain": json.dumps(explain.get(fx, [])),
                    "ingested_at": now(),
                    "kickoff": kickoffs.get(fx),
                }
            )
    ids = [el["id"] for el in live["elements"]]
    if ids:
        marks = ", ".join("?" for _ in ids)
        db.con.execute(
            f"DELETE FROM fpl_points_gw WHERE season = ? AND gw = ? AND fpl_id IN ({marks})",
            [season, gw, *ids],
        )
    n = db.upsert("fpl_points_gw", pd.DataFrame(rows))
    db.log("fpl", "fpl_points_gw", n,
           f"gw{gw} players={len(ids)} unknown_splits={unknown} blanks={placeholders}")  # fmt: skip
    return n
