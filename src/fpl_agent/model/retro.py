"""Retro: what the agent would decide for one gameweek today, set against the team you submitted.

The agent starts from your squad as it stood after the previous deadline, sees the database as it
was at the deadline (the latest snapshot taken before it, with the team news of that moment),
projects the horizon and decides with the current planner and chip settings. Both teams are then
scored on the official live points. It runs without ownership, bookmaker odds or the scout, so it
is the planner and projections on their own, not the whole live stack.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pandas as pd

from fpl_agent.data.entry import parse_squad
from fpl_agent.data.fpl_client import FPLClient
from fpl_agent.db.database import FootballDB
from fpl_agent.model import project, replay
from fpl_agent.rules import Rules
from fpl_agent.solver.inputs import to_canonical
from fpl_agent.solver.planner import WeekPlan


def deadline(raw: dict[str, Any], gw: int) -> datetime:
    """The gameweek's deadline as naive UTC, like the database's timestamps."""
    ev = next(e for e in raw["events"] if e["id"] == gw)
    return (
        datetime.fromisoformat(ev["deadline_time"].replace("Z", "+00:00"))
        .astimezone(UTC)
        .replace(tzinfo=None)
    )


def my_week(picks: dict[str, Any], fpl_to_pid: dict[int, int]) -> WeekPlan:
    """The submitted team as a plan: the XI, the bench in order (goalkeeper last), captains."""
    rows = sorted(picks["picks"], key=lambda r: r["position"])
    pid = [fpl_to_pid[r["element"]] for r in rows]
    cap = next(pid[i] for i, r in enumerate(rows) if r["is_captain"])
    vice = next(pid[i] for i, r in enumerate(rows) if r["is_vice_captain"])
    bench = pid[12:15] + pid[11:12]
    chip = picks.get("active_chip")
    return WeekPlan(0, pid, pid[:11], bench, cap, vice, [], [], 0, chip, 0, 0.0)


def live_points(live: dict[str, Any], fpl_to_pid: dict[int, int]):
    pts, mins = {}, {}
    for el in live["elements"]:
        if el["id"] in fpl_to_pid:
            pts[fpl_to_pid[el["id"]]] = int(el["stats"]["total_points"])
            mins[fpl_to_pid[el["id"]]] = int(el["stats"]["minutes"])
    return pts, mins


def _name(names: dict[int, str], pid: int) -> str:
    return names.get(pid, f"#{pid}")


def _xi_table(plan: WeekPlan, pts: dict[int, int], xp: dict[int, float],
              names: dict[int, str]) -> list[str]:  # fmt: skip
    rows = []
    for p in plan.xi:
        mark = " (C)" if p == plan.captain else " (VC)" if p == plan.vice else ""
        rows.append(f"{_name(names, p)}{mark}: {pts.get(p, 0)} pts, expected {xp.get(p, 0):.1f}")
    bench = ", ".join(f"{_name(names, p)} {pts.get(p, 0)}" for p in plan.bench)
    return [*rows, f"bench: {bench}"]


def run(db: FootballDB, season: int, gw: int, entry: int, cfg: replay.ReplayConfig,
        fpl: FPLClient | None = None) -> str:  # fmt: skip
    fpl = fpl or FPLClient()
    raw = fpl.bootstrap_raw()
    rules = Rules.nearest(season)
    cutoff = deadline(raw, gw)
    pool = project.current_pool(db, season, gw, as_of=cutoff)
    if pool.empty:
        return f"no player snapshot of GW{gw} from before its deadline ({cutoff:%d %b %H:%M} UTC)"
    prev = fpl.entry_picks_raw(entry, gw - 1) if gw > 1 else {"picks": []}
    state = parse_squad(prev, fpl.entry_transfers_raw(entry), fpl.entry_history_raw(entry),
                        raw, gw, rules)  # fmt: skip
    state = to_canonical(state, pool)
    res = replay.replay_season(db, season, [gw], cfg, rules, start=state,
                               pool_fn=lambda *_: pool)  # fmt: skip
    if not res.weeks:
        return f"GW{gw} could not be planned (no fixtures or projections in the database)"
    agent = res.weeks[0]
    plan: WeekPlan = agent.detail["plan"]
    xp: dict[int, float] = agent.detail["xp"]

    fpl_to_pid = dict(zip(pool["fpl_id"].astype(int), pool["player_id"].astype(int), strict=True))
    pos = dict(zip(pool["player_id"].astype(int), pool["position"], strict=True))
    names = dict(db.con.execute("SELECT player_id, name FROM players").fetchall())
    pts, mins = live_points(fpl.live_raw(gw), fpl_to_pid)
    mine_picks = fpl.entry_picks_raw(entry, gw)
    mine = my_week(mine_picks, fpl_to_pid)
    moves = [t for t in fpl.entry_transfers_raw(entry) if t["event"] == gw]
    hist = mine_picks.get("entry_history", {})
    my_hits = int(hist.get("event_transfers_cost", 0)) // 4

    mine_got, mine_cap, mine_bench = replay.score_week(mine, pts, mins, pos, rules)
    agent_got, agent_cap, agent_bench = replay.score_week(plan, pts, mins, pos, rules)
    mine_net, agent_net = mine_got - 4 * my_hits, agent_got - 4 * plan.hits
    official = hist.get("points")

    def move_line(ins: list[int], outs: list[int]) -> str:
        if not ins:
            return "no transfers"
        gain = sum(pts.get(p, 0) for p in ins) - sum(pts.get(p, 0) for p in outs)
        return (f"out {', '.join(f'{_name(names, p)} {pts.get(p, 0)}' for p in outs)}; "
                f"in {', '.join(f'{_name(names, p)} {pts.get(p, 0)}' for p in ins)} "
                f"({gain:+d} points from the moves)")  # fmt: skip

    my_in = [fpl_to_pid[t["element_in"]] for t in moves if t["element_in"] in fpl_to_pid]
    my_out = [fpl_to_pid[t["element_out"]] for t in moves if t["element_out"] in fpl_to_pid]
    both = set(plan.xi) & set(mine.xi)
    only_mine = [p for p in mine.xi if p not in both]
    only_agent = [p for p in plan.xi if p not in both]
    diff = sum(pts.get(p, 0) for p in only_agent) - sum(pts.get(p, 0) for p in only_mine)

    out = [
        f"GW{gw} retro: the current agent against the team you submitted",
        f"(squad after GW{gw - 1}, data as of the deadline {cutoff:%a %d %b %H:%M} UTC; no "
        "ownership, odds or scout)", "",
        f"points after hits: you {mine_net}"
        + (f" (FPL says {official})" if official is not None else "")
        + f", agent {agent_net}",
        f"chip: you {mine.chip or 'none'}, agent {plan.chip or 'none'}",
        f"hits: you {my_hits}, agent {plan.hits}",
        f"captain: you {_name(names, mine.captain)} {pts.get(mine.captain, 0)}, "
        f"agent {_name(names, plan.captain)} {pts.get(plan.captain, 0)} "
        f"(captain points counted: {mine_cap} against {agent_cap})",
        f"bench points left: you {mine_bench}, agent {agent_bench}",
        f"the agent expected {plan.xp:.1f} before hits and got {agent_got}", "",
        f"your moves: {move_line(my_in, my_out)}",
        f"agent moves: {move_line(agent.detail['ins'], agent.detail['outs'])}", "",
        "players only in your XI: "
        + (", ".join(f"{_name(names, p)} {pts.get(p, 0)} (exp {xp.get(p, 0):.1f})"
                     for p in only_mine) or "none"),
        "players only in the agent's XI: "
        + (", ".join(f"{_name(names, p)} {pts.get(p, 0)} (exp {xp.get(p, 0):.1f})"
                     for p in only_agent) or "none"),
        f"difference from the different XI players: {diff:+d} for the agent", "",
        "your XI:", *_xi_table(mine, pts, xp, names), "",
        "agent XI:", *_xi_table(plan, pts, xp, names),
    ]  # fmt: skip
    best = pd.Series({p: pts.get(p, 0) for p in xp}).sort_values(ascending=False).head(8)
    out += [
        "",
        "best scorers of the week among players projected: "
        + ", ".join(f"{_name(names, p)} {v} (exp {xp[p]:.1f})" for p, v in best.items()),
    ]
    return "\n".join(out)
