"""Read-only payloads for the dashboard, fixtures and player screens.

Everything here is a reshaping of public FPL data and the shared forecast; nothing is
predicted or optimised in this module. Missing inputs are reported as missing, never filled.
"""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

from fpl_agent.data.players import POSITIONS
from fpl_agent.web.forecasts import Shared
from fpl_agent.web.planning import PublicState, team_facts


def _teams(boot: dict) -> dict[int, dict]:
    return {t["id"]: t for t in boot.get("teams", [])}


def _el(boot: dict) -> dict[int, dict]:
    return {e["id"]: e for e in boot.get("elements", [])}


def manager(entry: dict) -> dict[str, Any]:
    """Public manager details shown after a lookup (FPL publishes these for every entry)."""
    overall = next(
        (lg for lg in (entry.get("leagues") or {}).get("classic", []) if lg.get("id") == 314), None
    )
    return {
        "entry_id": entry.get("id"),
        "team_name": entry.get("name"),
        "manager_name": " ".join(
            x for x in (entry.get("player_first_name"), entry.get("player_last_name")) if x
        )
        or None,
        "region": entry.get("player_region_name"),
        "started_event": entry.get("started_event"),
        "years_active": entry.get("years_active"),
        "overall_points": entry.get("summary_overall_points"),
        "overall_rank": entry.get("summary_overall_rank"),
        "event_points": entry.get("summary_event_points"),
        "event_rank": entry.get("summary_event_rank"),
        "current_event": entry.get("current_event"),
        "last_deadline_bank": entry.get("last_deadline_bank"),
        "last_deadline_value": entry.get("last_deadline_value"),
        "players_in_game": (overall or {}).get("rank_count"),
    }


def _fixtures_by_team(fixtures: list[dict], gws: list[int]) -> dict[int, dict[int, list]]:
    out: dict[int, dict[int, list]] = {}
    for f in fixtures:
        ev = f.get("event")
        if ev not in gws:
            continue
        out.setdefault(f["team_h"], {}).setdefault(ev, []).append(
            {
                "opp": f["team_a"],
                "home": True,
                "difficulty": f.get("team_h_difficulty"),
                "kickoff": f.get("kickoff_time"),
            }
        )
        out.setdefault(f["team_a"], {}).setdefault(ev, []).append(
            {
                "opp": f["team_h"],
                "home": False,
                "difficulty": f.get("team_a_difficulty"),
                "kickoff": f.get("kickoff_time"),
            }
        )
    return out


def _forecast_xp(shared: Shared | None, gw: int) -> dict[int, dict]:
    """FPL id -> next-gameweek forecast fields, when the forecast is for `gw`."""
    if shared is None or shared.proj.gws[0] != gw:
        return {}
    mean = shared.proj.mean()[:, 0]
    rows = dict(zip(shared.proj.player_ids, range(len(mean)), strict=True))
    out = {}
    fpl = dict(
        zip(
            shared.players["player_id"].astype(int),
            shared.players["fpl_id"].astype(int),
            strict=True,
        )
    )
    mt = shared.proj.minutes_table
    ps = {}
    if mt is not None:
        m = mt[mt["gw"] == gw]
        ps = dict(zip(m["player_id"].astype(int), m["p_start"].astype(float), strict=False))
    for pid, i in rows.items():
        if pid in fpl:
            out[fpl[pid]] = {"xp": round(float(mean[i]), 2), "p_start": ps.get(pid)}
    return out


def dashboard(state: PublicState, fixtures: list[dict], shared: Shared | None) -> dict[str, Any]:
    boot, gw = state.bootstrap, state.gw
    teams, els = _teams(boot), _el(boot)
    nxt = next((e for e in boot["events"] if e["id"] == gw), {})
    fx_gws = list(range(gw, min(gw + 5, 39)))
    by_team = _fixtures_by_team(fixtures, fx_gws)
    xp = _forecast_xp(shared, gw)
    picks = state.picks.get("picks") or []
    squad = []
    for p in sorted(picks, key=lambda x: x.get("position", 0)):
        e = els.get(p["element"], {})
        t = teams.get(e.get("team"), {})
        up = []
        for g in fx_gws:
            games = by_team.get(e.get("team"), {}).get(g, [])
            up.append(
                {
                    "gw": g,
                    "games": [
                        {
                            "opp": teams.get(x["opp"], {}).get("short_name"),
                            "home": x["home"],
                            "difficulty": x["difficulty"],
                        }
                        for x in games
                    ],
                }
            )
        squad.append(
            {
                "fpl_id": p["element"],
                "name": e.get("web_name"),
                "position": POSITIONS.get(e.get("element_type"), "?"),
                "team": t.get("short_name"),
                "team_name": t.get("name"),
                "slot": p.get("position"),
                "starting": p.get("position", 99) <= 11,
                "captain": bool(p.get("is_captain")),
                "vice": bool(p.get("is_vice_captain")),
                "multiplier": p.get("multiplier"),
                "price": e.get("now_cost"),
                "status": e.get("status"),
                "chance": e.get("chance_of_playing_next_round"),
                "news": e.get("news") or "",
                "form": e.get("form"),
                "total_points": e.get("total_points"),
                "next": xp.get(p["element"]),
                "fixtures": up,
            }
        )
    hist = state.history
    rank_history = [
        {
            "gw": r["event"],
            "points": r["points"],
            "total": r["total_points"],
            "overall_rank": r.get("overall_rank"),
            "gw_rank": r.get("rank"),
            "transfers": r.get("event_transfers"),
            "hit_cost": r.get("event_transfers_cost"),
            "bench_points": r.get("points_on_bench"),
            "bank": r.get("bank"),
            "value": r.get("value"),
        }
        for r in hist.get("current", [])
    ]
    picks_gw = gw - 1
    return {
        "manager": manager(state.entry),
        "gameweek": {
            "next": gw,
            "deadline": nxt.get("deadline_time"),
            "name": nxt.get("name"),
            "squad_gameweek": picks_gw,
        },
        "facts": team_facts(state),
        "squad": squad,
        "squad_label": f"Latest confirmed public squad: the GW{picks_gw} deadline squad",
        "active_chip": state.picks.get("active_chip"),
        "rank_history": rank_history,
        "chips_used": hist.get("chips", []),
        "past_seasons": hist.get("past", []),
        "fixture_gws": fx_gws,
        "sources": state.sources,
        "forecast": None
        if shared is None
        else {
            "key": shared.key,
            "gw": shared.proj.gws[0],
            "cutoff": shared.manifest["cutoff"],
            "matches_next": shared.proj.gws[0] == gw,
        },
    }


def fixtures_view(
    boot: dict, fixtures: list[dict], shared: Shared | None, n: int = 6
) -> dict[str, Any]:
    """Every club's next `n` gameweeks: opponent, venue, FPL difficulty and, inside the
    forecast's horizon, the model's expected goals for and against."""
    nxt = next((e["id"] for e in boot["events"] if e.get("is_next")), None)
    if nxt is None:
        return {"gws": [], "teams": [], "note": "no upcoming gameweek"}
    gws = list(range(nxt, min(nxt + n, 39)))
    teams = _teams(boot)
    by_team = _fixtures_by_team(fixtures, gws)
    lam: dict[tuple[int, int, int], tuple[float, float]] = {}
    can = {}
    if shared is not None:  # canonical team id -> FPL team id
        tm = shared.players[["team_id", "fpl_id"]]
        els = _el(boot)
        for r in tm.itertuples():
            e = els.get(int(r.fpl_id))
            if e:
                can[int(r.team_id)] = e["team"]
        for g, rows in shared.proj.fixtures.items():
            for h, a, _ok, lh, la in rows:
                if h in can and a in can:
                    lam[(g, can[h], can[a])] = (float(lh), float(la))
    out = []
    for tid, t in sorted(teams.items(), key=lambda kv: kv[1]["name"]):
        row = {"id": tid, "name": t["name"], "short": t["short_name"], "gws": []}
        for g in gws:
            games = []
            for x in by_team.get(tid, {}).get(g, []):
                key = (g, tid, x["opp"]) if x["home"] else (g, x["opp"], tid)
                lm = lam.get(key)
                xg_for = xg_against = None
                if lm:
                    xg_for, xg_against = (lm[0], lm[1]) if x["home"] else (lm[1], lm[0])
                games.append(
                    {
                        "opp": teams.get(x["opp"], {}).get("short_name"),
                        "home": x["home"],
                        "difficulty": x["difficulty"],
                        "kickoff": x["kickoff"],
                        "xg_for": None if xg_for is None else round(xg_for, 2),
                        "xg_against": None if xg_against is None else round(xg_against, 2),
                        "cs_chance": None
                        if xg_against is None
                        else round(float(np.exp(-xg_against)), 3),
                    }
                )
            row["gws"].append({"gw": g, "games": games})
        out.append(row)
    return {
        "gws": gws,
        "teams": out,
        "forecast_gws": list(shared.proj.gws) if shared is not None else [],
        "note": "Expected goals come from the team-strength model (blended with bookmaker "
        "odds where available); clean-sheet chance assumes Poisson scoring. FPL "
        "difficulty is FPL's own 1-5 rating.",
    }


def players_view(
    shared: Shared,
    position: str | None = None,
    q: str | None = None,
    team: str | None = None,
    max_price: int | None = None,
    sort: str = "xp",
    limit: int = 60,
    ids: list[int] | None = None,
) -> dict[str, Any]:
    proj = shared.proj
    mean = proj.mean()
    p10 = (proj.points >= 10).mean(axis=2)
    q10, q90 = np.quantile(proj.points[:, 0, :], [0.1, 0.9], axis=1)
    mins = proj.minutes.mean(axis=2)
    rows = {p: i for i, p in enumerate(proj.player_ids)}
    mt = proj.minutes_table
    mt0 = mt[mt["gw"] == proj.gws[0]].set_index("player_id") if mt is not None else None
    comps = {k: v[:, 0] for k, v in proj.components.items()}
    df = shared.players.copy()
    if ids:
        df = df[df["player_id"].isin(ids)]
    if position:
        df = df[df["position"] == position]
    if team:
        df = df[df["team"] == team]
    if max_price:
        df = df[df["price"] <= max_price]
    if q:
        ql = q.strip().lower()
        df = df[
            df["name"].str.lower().str.contains(ql, regex=False)
            | df["full_name"].fillna("").str.lower().str.contains(ql, regex=False)
        ]
    out = []
    for r in df.itertuples():
        i = rows.get(int(r.player_id))
        f = {
            "id": int(r.player_id),
            "fpl_id": int(r.fpl_id),
            "name": r.name,
            "team": r.team,
            "position": r.position,
            "price": int(r.price),
            "status": r.status,
            "chance": None if pd.isna(r.chance) else float(r.chance),
            "news": r.news,
            "owned": r.selected_by_percent,
            "stats": {
                k: (None if pd.isna(getattr(r, k)) else float(getattr(r, k)))
                for k in (
                    "minutes",
                    "starts",
                    "total_points",
                    "form",
                    "goals_scored",
                    "assists",
                    "expected_goals",
                    "expected_assists",
                    "expected_goals_per_90",
                    "expected_assists_per_90",
                    "defensive_contribution_per_90",
                    "clean_sheets",
                    "bonus",
                    "penalties_order",
                    "ep_next",
                )
            },
            "projected": i is not None,
        }
        if i is not None:
            f["xp"] = [round(float(mean[i, g]), 2) for g in range(len(proj.gws))]
            f["xp_total"] = round(float(mean[i].sum()), 2)
            f["exp_minutes"] = round(float(mins[i, 0]), 1)
            f["p_haul"] = round(float(p10[i, 0]), 3)
            f["range"] = [round(float(q10[i]), 1), round(float(q90[i]), 1)]
            f["components"] = {k: round(float(v[i]), 3) for k, v in comps.items()}
            if mt0 is not None and int(r.player_id) in mt0.index:
                m = mt0.loc[int(r.player_id)]
                if isinstance(m, pd.DataFrame):
                    m = m.iloc[0]
                f["minutes_model"] = {
                    "p_start": round(float(m["p_start"]), 3),
                    "p_60": round(float(m["p_start"] * m["p_long"]), 3),
                    "p_cameo": round(float((1 - m["p_start"]) * m["p_cameo"]), 3),
                    "p_none": round(float((1 - m["p_start"]) * (1 - m["p_cameo"])), 3),
                    "availability": round(float(m["availability"]), 3),
                }
        out.append(f)
    key = {
        "xp": lambda x: -(x.get("xp") or [0])[0],
        "xp_total": lambda x: -x.get("xp_total", 0),
        "price": lambda x: -x["price"],
        "owned": lambda x: -(x["owned"] or 0),
        "value": lambda x: -((x.get("xp_total") or 0) / max(x["price"], 1)),
    }.get(sort, lambda x: -(x.get("xp") or [0])[0])
    out.sort(key=key)
    return {
        "gws": list(proj.gws),
        "players": out[:limit],
        "total": len(out),
        "missing": {
            "news": "Team news text is FPL's; no other news source is used.",
            "minutes": "Start, 60-minute, cameo and no-appearance chances come from "
            "the minutes model with FPL's availability flag applied; "
            "'available' is not treated as a guaranteed start.",
            "stats": "Season totals are FPL's; per-90 rates are blank for players without minutes.",
        },
    }


__all__ = ["dashboard", "fixtures_view", "players_view", "manager"]
