"""L6 multi-week planner: a MILP over a horizon of gameweeks (HiGHS).

For every gameweek in the horizon it chooses the squad, the starting XI, bench order, captain and
the transfers needed to get there, subject to FPL's rules (2/5/5/3, three per club, formation,
budget with selling prices, free-transfer banking up to five, -4 per extra transfer). It values:

* every starter's expected points, the captain's extra multiple, and a weighted bench (the first
  sub can actually cover a starter who doesn't play);
* a banked free transfer by how many are banked (the 2nd is worth more than the 5th), so it will
  roll one rather than waste it, and spend a 5th rather than lose it;
* money left in the bank a little;
* hits at their face value plus a buffer: a -4 must buy about six points.

Chips are parameters of a solve (see `chips.py` for the scenario search): a Wildcard week has free
unlimited transfers and keeps the banked count (adding none that week); a Bench Boost week scores
the whole bench; a Triple Captain week multiplies the captain by three; in a Free Hit week the
squad is a separate one-week team (valued by `free_hit_value`) and the real squad is untouched.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from fpl_agent.solver.mip import Infeasible, Model

SQUAD_COUNTS = {"GKP": 2, "DEF": 5, "MID": 5, "FWD": 3}
FORMATION_MIN = {"GKP": 1, "DEF": 3, "MID": 2, "FWD": 1}
FORMATION_MAX = {"GKP": 1, "DEF": 5, "MID": 5, "FWD": 3}
CHIPS = ("wildcard", "freehit", "bboost", "3xc")


POSITION_ORDER = {"GKP": 0, "DEF": 1, "MID": 2, "FWD": 3}


@dataclass(frozen=True)
class PlannerConfig:
    decay: float = 0.88
    bench_weights: tuple[float, float, float] = (0.25, 0.10, 0.03)
    bench_gk_weight: float = 0.03
    ft_values: tuple[float, ...] = (2.0, 1.6, 1.3, 1.1)  # value of the 2nd, 3rd, 4th, 5th banked FT
    hit_cost: float = 4.0
    hit_buffer: float = 2.0  # a hit must buy hit_cost + buffer expected points
    max_hits_per_gw: int = 1
    max_hits_per_4gw: int = 2
    transfer_epsilon: float = 0.02  # tiny cost per transfer: no churn for nothing
    bank_value_per_tenth: float = 0.01  # ~0.1 point per £1m left in the bank
    max_free: int = 5
    club_limit: int = 3
    time_limit: float = 60.0
    gap: float = 1e-3
    # rank risk: each week, minus risk_lambda x a player's points spread (sd) x how far your
    # multiplier on him (XI 1, captain 2) is from the field's effective ownership. Positive
    # values hug the template (owning what the top managers own); 0 ignores the field.
    risk_lambda: float = 0.0


@dataclass
class SquadPlayer:
    player_id: int
    position: str
    team_id: int
    buy_price: int  # tenths of £m
    sell_price: int


@dataclass
class Candidate:
    player_id: int
    position: str
    team_id: int
    price: int
    xp: list[float]  # expected points per gameweek of the horizon
    sd: list[float] | None = None  # spread of those points (standard deviation), if known


@dataclass
class PlanInput:
    gws: list[int]
    squad: list[SquadPlayer]
    bank: int
    free_transfers: int
    candidates: list[Candidate]
    chips: dict[int, str] = field(default_factory=dict)  # gw -> chip played that week
    free_hit_values: dict[int, float] = field(default_factory=dict)
    no_transfer_gws: frozenset[int] = frozenset()  # forced roll (used to price "do nothing")
    locked: frozenset[int] = frozenset()  # squad players that cannot be sold in the first week
    kept: frozenset[int] = frozenset()  # squad players not sold in any week of the plan
    eo: dict[int, float] = field(default_factory=dict)  # field effective ownership, 1.0 = 100%


@dataclass
class WeekPlan:
    gw: int
    squad: list[int]
    xi: list[int]
    bench: list[int]  # outfield bench in order, then the bench goalkeeper last
    captain: int
    vice: int
    transfers_in: list[int]
    transfers_out: list[int]
    hits: int
    chip: str | None
    ft_before: int
    xp: float  # this week's expected points of the plan (before hits)


@dataclass
class Plan:
    weeks: list[WeekPlan]
    objective: float
    bank: int
    ft_end: int

    @property
    def first(self) -> WeekPlan:
        return self.weeks[0]

    def total_hits(self) -> int:
        return sum(w.hits for w in self.weeks)


def solve_plan(pi: PlanInput, cfg: PlannerConfig | None = None) -> Plan:
    cfg = cfg or PlannerConfig()
    H = len(pi.gws)
    cands = {c.player_id: c for c in pi.candidates}
    squad = {p.player_id: p for p in pi.squad}
    missing = set(squad) - set(cands)
    if missing:
        raise ValueError(f"current squad players missing from candidates: {sorted(missing)}")
    ids = list(cands)
    by_pos = {pos: [i for i in ids if cands[i].position == pos] for pos in SQUAD_COUNTS}
    by_club: dict[int, list[int]] = {}
    for i in ids:
        by_club.setdefault(cands[i].team_id, []).append(i)
    outfield = [i for i in ids if cands[i].position != "GKP"]
    gks = by_pos["GKP"]

    m = Model()
    chip_at = {t: pi.chips.get(pi.gws[t]) for t in range(H)}
    fh = {t for t in range(H) if chip_at[t] == "freehit"}
    wc = {t for t in range(H) if chip_at[t] == "wildcard"}
    bb = {t for t in range(H) if chip_at[t] == "bboost"}
    tc = {t for t in range(H) if chip_at[t] == "3xc"}
    forced_roll = {t for t in range(H) if pi.gws[t] in pi.no_transfer_gws}

    for t in range(H):
        frozen = t in fh or t in forced_roll
        for i in ids:
            m.var(("s", i, t))
            m.var(("x", i, t))
            m.var(("c", i, t))
            m.var(("in", i, t), ub=0.0 if frozen else 1.0)
            locked = (t == 0 and i in pi.locked) or i in pi.kept
            m.var(("out", i, t), ub=0.0 if frozen or locked else 1.0)
        for i in outfield:
            for k in range(3):
                m.var(("b", i, t, k))
        m.var(("bank", t), ub=1e6, integer=False)
        no_hit = t in wc or t in fh or t in forced_roll
        m.var(("hit", t), ub=0.0 if no_hit else cfg.max_hits_per_gw, integer=True)
        m.var(("use", t), ub=cfg.max_free, integer=True)  # free transfers used
        m.var(("ft", t + 1), lb=1, ub=cfg.max_free, integer=True)
    ft0 = min(max(pi.free_transfers, 1), cfg.max_free)

    for t in range(H):
        for i in ids:
            prev = 1.0 if (t == 0 and i in squad) else 0.0
            flow = {("s", i, t): 1.0, ("in", i, t): -1.0, ("out", i, t): 1.0}
            if t == 0:
                m.eq(flow, prev)
            else:
                m.eq({**flow, ("s", i, t - 1): -1.0}, 0.0)
            m.le({("in", i, t): 1.0, ("out", i, t): 1.0}, 1.0)
            m.le({("x", i, t): 1.0, ("s", i, t): -1.0}, 0.0)
            m.le({("c", i, t): 1.0, ("x", i, t): -1.0}, 0.0)
        for pos, n in SQUAD_COUNTS.items():
            m.eq({("s", i, t): 1.0 for i in by_pos[pos]}, n)
            lo, hi = FORMATION_MIN[pos], FORMATION_MAX[pos]
            m.ge({("x", i, t): 1.0 for i in by_pos[pos]}, lo)
            m.le({("x", i, t): 1.0 for i in by_pos[pos]}, hi)
        for members in by_club.values():
            if len(members) > cfg.club_limit:
                m.le({("s", i, t): 1.0 for i in members}, cfg.club_limit)
        m.eq({("x", i, t): 1.0 for i in ids}, 11)
        m.eq({("c", i, t): 1.0 for i in ids}, 1)
        for i in outfield:  # every non-starting outfield player takes exactly one bench slot
            m.eq({("b", i, t, 0): 1.0, ("b", i, t, 1): 1.0, ("b", i, t, 2): 1.0,
                  ("s", i, t): -1.0, ("x", i, t): 1.0}, 0.0)  # fmt: skip
        for k in range(3):
            m.eq({("b", i, t, k): 1.0 for i in outfield}, 1)
        # money
        bank = {("bank", t): 1.0}
        for i in ids:
            sell = squad[i].sell_price if i in squad else cands[i].price
            bank[("out", i, t)] = -float(sell)
            bank[("in", i, t)] = float(cands[i].price)
        if t == 0:
            m.eq(bank, float(pi.bank))
        else:
            m.eq({**bank, ("bank", t - 1): -1.0}, 0.0)
        # transfers, hits and the free-transfer bank
        n_t = {("in", i, t): 1.0 for i in ids}
        if t in wc:
            m.eq({("use", t): 1.0}, 0.0)
        else:
            m.eq({**n_t, ("hit", t): -1.0, ("use", t): -1.0}, 0.0)
        # a Wildcard or Free Hit week keeps the bank as it was but adds no new transfer
        accrue = 0.0 if t in wc or t in fh else 1.0
        if t == 0:
            m.le({("use", 0): 1.0}, ft0)
            m.le({("ft", 1): 1.0, ("use", 0): 1.0}, ft0 + accrue)
        else:
            m.le({("use", t): 1.0, ("ft", t): -1.0}, 0.0)
            m.le({("ft", t + 1): 1.0, ("use", t): 1.0, ("ft", t): -1.0}, accrue)
        if t in fh:  # a Free Hit does not use up the banked transfers either
            m.eq({("use", t): 1.0}, 0.0)
    for t in range(H - 3):  # no more than max_hits_per_4gw hits in any four-week window
        m.le({("hit", u): 1.0 for u in range(t, t + 4)}, cfg.max_hits_per_4gw)

    # banked-transfer value at the end of the horizon (concave, so LP fills the cheap tier first)
    d_end = cfg.decay**H
    for k, v in enumerate(cfg.ft_values):
        m.var(("fv", k), ub=1.0, integer=False, obj=d_end * v)
    m.le({**{("fv", k): 1.0 for k in range(len(cfg.ft_values))}, ("ft", H): -1.0}, -1.0)
    m.add_obj(("bank", H - 1), d_end * cfg.bank_value_per_tenth)

    # objective
    for t in range(H):
        d = cfg.decay**t
        if t in fh:
            continue  # the real squad scores nothing this week; the Free Hit team is a constant
        mult = 3.0 if t in tc else 2.0
        bw = (1.0, 1.0, 1.0) if t in bb else cfg.bench_weights
        gk_w = 1.0 if t in bb else cfg.bench_gk_weight
        for i in ids:
            xp = cands[i].xp[t]
            m.add_obj(("x", i, t), d * xp)
            m.add_obj(("c", i, t), d * xp * (mult - 1.0))
            if cands[i].position == "GKP":
                m.add_obj(("s", i, t), d * gk_w * xp)
                m.add_obj(("x", i, t), -d * gk_w * xp)
            else:
                for k in range(3):
                    m.add_obj(("b", i, t, k), d * bw[k] * xp)
            m.add_obj(("in", i, t), -d * cfg.transfer_epsilon)
        m.add_obj(("hit", t), -d * (cfg.hit_cost + cfg.hit_buffer))
    if cfg.risk_lambda and pi.eo:  # |your multiplier - field EO| x spread, made linear
        for t in range(H):
            if t in fh:
                continue
            d = cfg.decay**t
            extra = (3.0 if t in tc else 2.0) - 1.0
            for i in ids:
                sd = cands[i].sd[t] if cands[i].sd else 0.0
                if sd <= 0:
                    continue
                e = pi.eo.get(i, 0.0)
                play = ("s", i, t) if t in bb else ("x", i, t)  # a Bench Boost plays all 15
                m.var(("dev", i, t), ub=3.0, integer=False, obj=-d * cfg.risk_lambda * sd)
                m.ge({("dev", i, t): 1.0, play: -1.0, ("c", i, t): -extra}, -e)
                m.ge({("dev", i, t): 1.0, play: 1.0, ("c", i, t): extra}, e)
    constant = sum(cfg.decay**t * pi.free_hit_values.get(pi.gws[t], 0.0) for t in fh)

    sol, obj = m.solve(cfg.time_limit, cfg.gap)
    on = lambda k: sol[k] > 0.5  # noqa: E731
    weeks = []
    ft_prev = ft0
    for t in range(H):
        sq = [i for i in ids if on(("s", i, t))]
        xi = [i for i in ids if on(("x", i, t))]
        bench_out = [next(i for i in outfield if on(("b", i, t, k))) for k in range(3)]
        bench_gk = [i for i in gks if on(("s", i, t)) and not on(("x", i, t))]
        xp_t = lambda i, t=t: cands[i].xp[t]  # noqa: E731
        cap = next(i for i in ids if on(("c", i, t)))
        others = sorted((i for i in xi if i != cap), key=lambda i: -xp_t(i))
        line = lambda i: (POSITION_ORDER.get(cands[i].position, 4), i)  # noqa: E731
        weeks.append(WeekPlan(
            gw=pi.gws[t], squad=sq, xi=xi, bench=bench_out + bench_gk, captain=cap,
            vice=others[0],
            # by position, so the n-th player in and out are a legal same-position swap
            transfers_in=sorted((i for i in ids if on(("in", i, t))), key=line),
            transfers_out=sorted((i for i in ids if on(("out", i, t))), key=line),
            hits=round(sol[("hit", t)]), chip=chip_at[t], ft_before=ft_prev,
            xp=sum(xp_t(i) for i in (sq if t in bb else xi))
            + xp_t(cap) * (2.0 if t in tc else 1.0),
        ))  # fmt: skip
        ft_prev = round(sol[("ft", t + 1)])
    return Plan(weeks, obj + constant, round(sol[("bank", H - 1)]), ft_prev)


def _free_hit_solve(cands: list[Candidate], t: int, budget: int, cfg: PlannerConfig,
                    club_limit: int = 3):  # fmt: skip
    ids = [c.player_id for c in cands]
    cd = {c.player_id: c for c in cands}
    by_pos = {pos: [i for i in ids if cd[i].position == pos] for pos in SQUAD_COUNTS}
    outfield = [i for i in ids if cd[i].position != "GKP"]
    m = Model()
    for i in ids:
        m.var(("s", i))
        m.var(("x", i))
        m.var(("c", i))
    for i in outfield:
        for k in range(3):
            m.var(("b", i, k))
    for i in ids:
        m.le({("x", i): 1.0, ("s", i): -1.0}, 0.0)
        m.le({("c", i): 1.0, ("x", i): -1.0}, 0.0)
    for pos, n in SQUAD_COUNTS.items():
        m.eq({("s", i): 1.0 for i in by_pos[pos]}, n)
        m.ge({("x", i): 1.0 for i in by_pos[pos]}, FORMATION_MIN[pos])
        m.le({("x", i): 1.0 for i in by_pos[pos]}, FORMATION_MAX[pos])
    clubs: dict[int, list[int]] = {}
    for i in ids:
        clubs.setdefault(cd[i].team_id, []).append(i)
    for members in clubs.values():
        if len(members) > club_limit:
            m.le({("s", i): 1.0 for i in members}, club_limit)
    m.eq({("x", i): 1.0 for i in ids}, 11)
    m.eq({("c", i): 1.0 for i in ids}, 1)
    for i in outfield:
        m.eq(
            {("b", i, 0): 1.0, ("b", i, 1): 1.0, ("b", i, 2): 1.0, ("s", i): -1.0, ("x", i): 1.0},
            0.0,
        )
    for k in range(3):
        m.eq({("b", i, k): 1.0 for i in outfield}, 1)
    m.le({("s", i): float(cd[i].price) for i in ids}, float(budget))
    for i in ids:
        xp = cd[i].xp[t]
        m.add_obj(("x", i), xp)
        m.add_obj(("c", i), xp)
        if cd[i].position == "GKP":
            m.add_obj(("s", i), cfg.bench_gk_weight * xp)
            m.add_obj(("x", i), -cfg.bench_gk_weight * xp)
        else:
            for k in range(3):
                m.add_obj(("b", i, k), cfg.bench_weights[k] * xp)
    sol, obj = m.solve(cfg.time_limit, cfg.gap)
    return obj, sol, ids, outfield, cd


def free_hit_value(cands: list[Candidate], t: int, budget: int, cfg: PlannerConfig | None = None,
                   club_limit: int = 3) -> tuple[float, list[int]]:  # fmt: skip
    """Best one-week team (a Free Hit squad) for week index `t` within `budget` (tenths).

    Scored like a normal week: XI, captain doubled, weighted bench. Returns (points, squad ids).
    """
    obj, sol, ids, _, _ = _free_hit_solve(cands, t, budget, cfg or PlannerConfig(), club_limit)
    return obj, [i for i in ids if sol[("s", i)] > 0.5]


def free_hit_team(cands: list[Candidate], t: int, gw: int, budget: int,
                  cfg: PlannerConfig | None = None) -> tuple[float, WeekPlan]:  # fmt: skip
    """The Free Hit team as a full week: squad, XI, bench order, captain and vice."""
    obj, sol, ids, outfield, cd = _free_hit_solve(cands, t, budget, cfg or PlannerConfig(), 3)
    on = lambda k: sol[k] > 0.5  # noqa: E731
    sq = [i for i in ids if on(("s", i))]
    xi = [i for i in ids if on(("x", i))]
    bench = [next(i for i in outfield if on(("b", i, k))) for k in range(3)]
    bench += [i for i in sq if cd[i].position == "GKP" and i not in xi]
    cap = next(i for i in ids if on(("c", i)))
    vice = sorted((i for i in xi if i != cap), key=lambda i: -cd[i].xp[t])[0]
    wk = WeekPlan(gw, sq, xi, bench, cap, vice, [], [], 0, "freehit", 0,
                  sum(cd[i].xp[t] for i in xi) + cd[cap].xp[t])  # fmt: skip
    return obj, wk


__all__ = ["Infeasible", "PlannerConfig", "Plan", "PlanInput", "Candidate", "SquadPlayer",
           "WeekPlan", "solve_plan", "free_hit_value", "free_hit_team", "CHIPS"]  # fmt: skip
