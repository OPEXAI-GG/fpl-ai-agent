"""L7 safety rails. Pure checks run before anything is written, on our own plan *and* again on
FPL's preview of it. A violation in the transfer group blocks transfers; a violation in the
lineup group blocks the lineup update; `global` blocks everything. Nothing here talks to FPL.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd

from fpl_agent.config import Settings
from fpl_agent.data.entry import SquadState
from fpl_agent.rules import Rules
from fpl_agent.solver.decide import Decision

MIN_CHANCE = 75  # never buy or start a player below this chance of playing
MAX_TRANSFERS_OUTSIDE_CHIP = 3
MAX_HITS_PER_GW = 1
MAX_HITS_PER_WINDOW = 2  # i.e. -8 over any four gameweeks
HIT_WINDOW = 4
MAX_MISSING_PROJECTIONS = 0.05
XP_BOUNDS = (-2.0, 25.0)
WILDCARD_MIN_WIN = 0.70
TC_MIN_XP = 8.0
SQUAD = {"GKP": 2, "DEF": 5, "MID": 5, "FWD": 3}


@dataclass(frozen=True)
class Violation:
    group: str  # "global" | "transfers" | "lineup"
    rule: str
    detail: str

    def __str__(self) -> str:
        return f"[{self.group}] {self.rule}: {self.detail}"


@dataclass
class Context:
    settings: Settings
    state: SquadState
    pool: pd.DataFrame  # player_id, team_id, position, status, chance
    xp: dict[int, float]  # this gameweek's expected points by player
    bootstrap_next_gw: int | None = None
    recent_hits: dict[int, int] = field(default_factory=dict)  # gw -> hits taken (last 4 GWs)
    projected_share: float = 1.0  # share of the pool with a projection
    double_gw_players: frozenset[int] = frozenset()


@dataclass
class Verdict:
    violations: list[Violation]

    def blocked(self, group: str) -> bool:
        return any(v.group in (group, "global") for v in self.violations)

    @property
    def ok(self) -> bool:
        return not self.violations


def _bad_availability(pool: pd.DataFrame, ids: list[int]) -> list[int]:
    rows = pool.set_index("player_id")
    bad = []
    for i in ids:
        if i not in rows.index:
            bad.append(i)
            continue
        r = rows.loc[i]
        chance = 100.0 if pd.isna(r["chance"]) else float(r["chance"])
        if r["status"] != "a" or chance < MIN_CHANCE:
            bad.append(i)
    return bad


def resulting_squad(state: SquadState, d: Decision) -> list[int]:
    now = {p.player_id for p in state.squad}
    return sorted((now - set(d.transfers_out)) | set(d.transfers_in))


def check(d: Decision, ctx: Context, rules: Rules) -> Verdict:
    v: list[Violation] = []
    add = lambda g, r, t: v.append(Violation(g, r, t))  # noqa: E731
    s = ctx.settings
    if not s.enabled:
        add("global", "kill_switch", "AGENT_ENABLED=false")
    free_week = d.chip in ("wildcard", "freehit")

    # data sanity
    if ctx.bootstrap_next_gw is not None and ctx.bootstrap_next_gw != d.gw:
        add(
            "global",
            "stale_data",
            f"plan is for GW{d.gw} but FPL's next gameweek is GW{ctx.bootstrap_next_gw}",
        )
    if (1 - ctx.projected_share) > MAX_MISSING_PROJECTIONS:
        add(
            "global", "missing_projections", f"{1 - ctx.projected_share:.0%} of players unprojected"
        )
    lo, hi = XP_BOUNDS
    odd = [p for p in d.xi + d.bench if not lo <= ctx.xp.get(p, 0.0) <= hi]
    if odd:
        add("global", "xp_out_of_bounds", f"players {odd}")

    # the plan must be a legal squad
    squad = resulting_squad(ctx.state, d) if not d.chip == "freehit" else list(d.week.squad)
    pos = dict(zip(ctx.pool["player_id"], ctx.pool["position"], strict=False))
    team = dict(zip(ctx.pool["player_id"], ctx.pool["team_id"], strict=False))
    if d.chip != "freehit" or d.week.squad:
        if len(squad) != 15 or any(p not in pos for p in squad):
            add("global", "squad_size", f"{len(squad)} players, or unknown ids")
        else:
            for p, n in SQUAD.items():
                if sum(pos[i] == p for i in squad) != n:
                    add("global", "squad_shape", f"{p} count is not {n}")
            if max(pd.Series([team[i] for i in squad]).value_counts()) > rules["squad"]["per_club"]:
                add("global", "club_limit", "more than three players from one club")
    if d.bank_after < 0:
        add("transfers", "budget", f"bank would be {d.bank_after}")
    if len(d.xi) != 11 or d.captain not in d.xi or d.vice not in d.xi or d.captain == d.vice:
        add("global", "lineup_shape", "XI/captain/vice invalid")

    # transfers
    n = len(d.transfers_in)
    if n != len(d.transfers_out):
        add("transfers", "unbalanced", "transfers in and out differ")
    if n and ctx.state.already_transferred:
        add("transfers", "already_transferred", "transfers already made for this gameweek")
    if n > MAX_TRANSFERS_OUTSIDE_CHIP and not free_week:
        add("transfers", "transfer_count", f"{n} transfers outside a Wildcard/Free Hit")
    if not free_week:
        if d.hits > MAX_HITS_PER_GW:
            add("transfers", "hit_cap_gw", f"{d.hits} hits this gameweek")
        window = sum(h for gw, h in ctx.recent_hits.items() if d.gw - HIT_WINDOW < gw < d.gw)
        if window + d.hits > MAX_HITS_PER_WINDOW:
            add("transfers", "hit_cap_window", f"{window + d.hits} hits in {HIT_WINDOW} gameweeks")
    bad_in = _bad_availability(ctx.pool, d.transfers_in)
    if bad_in:
        add("transfers", "availability_buy", f"buying unavailable/doubtful players {bad_in}")

    # lineup
    bad_xi = _bad_availability(ctx.pool, d.xi)
    if bad_xi:
        add("lineup", "availability_start", f"starting unavailable/doubtful players {bad_xi}")

    # chips
    if d.chip:
        avail = ctx.state.chips_available(rules)
        if d.chip not in avail or not avail[d.chip][0] <= d.gw <= avail[d.chip][1]:
            add("global", "chip_unavailable", f"{d.chip} is not available in GW{d.gw}")
        if d.chip == "wildcard":
            wins = [s["win_share"] for s in d.chip_report.get("scenarios", [])
                    if s["chip"] == "wildcard" and s["gw"] == d.gw]  # fmt: skip
            if not wins or wins[0] < WILDCARD_MIN_WIN:
                add("global", "wildcard_sensitivity", "Wildcard did not win 70% of sampled worlds")
        if (
            d.chip == "3xc"
            and ctx.xp.get(d.captain, 0.0) < TC_MIN_XP
            and d.captain not in ctx.double_gw_players
        ):
            add(
                "global",
                "triple_captain",
                "captain is neither a double-gameweek player nor above the xP bar",
            )
    return Verdict(v)
