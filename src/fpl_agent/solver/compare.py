"""This week's transfer count, compared through the planner itself.

The planner's preferred plan can fail the robustness policy (it only wins on the mean). Rolling
is then not the only alternative: a smaller move may pass. So every feasible count of transfers
this week, from zero up to the planner's own maximum (the free transfers plus the hits a week
allows), is solved as a constraint of the same MILP, with the same candidates, budget, selling
prices, hit costs, kept players and configuration, and scored against rolling in the same
sampled worlds. The best plan by the planner's objective that the planner prefers to rolling
and that passes the policy is chosen; if none does, the transfer is rolled.

Nothing is enumerated by brute force and no threshold is relaxed: the count is the only thing
that differs between the options.
"""

from __future__ import annotations

from dataclasses import dataclass, replace

import numpy as np

from fpl_agent.model.project import Projection
from fpl_agent.rules import Rules
from fpl_agent.solver import chips as chips_mod
from fpl_agent.solver.mip import Infeasible
from fpl_agent.solver.planner import Plan, PlanInput, PlannerConfig, solve_plan


@dataclass
class Option:
    """One transfer count: its plan (None when infeasible) and how it scored."""

    transfers: int
    plan: Plan | None
    hits: int = 0
    week_xp: float = 0.0  # this week's expected points, minus hits
    horizon_xp: float = 0.0  # expected points over the horizon (undiscounted), minus hits
    objective: float | None = None  # the planner's full objective (discounted, bench, banking)
    win_share: float | None = None  # sampled worlds in which it beats rolling
    gain: float | None = None  # mean sampled-world points over rolling, across the horizon
    passes: bool = False  # preferred to rolling by the planner and passes the policy
    reason: str = ""
    solve: str = "optimal"

    def row(self) -> dict:
        return {
            "transfers": self.transfers, "hits": self.hits, "feasible": self.plan is not None,
            "week_xp": round(self.week_xp, 2), "horizon_xp": round(self.horizon_xp, 2),
            "objective": None if self.objective is None else round(self.objective, 2),
            "win_share": None if self.win_share is None else round(self.win_share, 3),
            "gain": None if self.gain is None else round(self.gain, 2),
            "passes": self.passes, "reason": self.reason, "solve": self.solve,
            "in": self.plan.first.transfers_in if self.plan else [],
            "out": self.plan.first.transfers_out if self.plan else [],
        }  # fmt: skip


@dataclass
class Comparison:
    options: list[Option]
    chosen: Option
    min_win: float
    min_gain: float
    worlds: int

    def rows(self) -> list[dict]:
        return [o.row() for o in self.options]


def _xp(plan: Plan) -> tuple[float, float]:
    week = plan.first.xp - 4 * plan.first.hits
    return week, sum(w.xp - 4 * w.hits for w in plan.weeks)


def max_transfers(base: PlanInput, cfg: PlannerConfig) -> int:
    """The planner's own ceiling on this week's transfers: the usable free transfers plus the
    hits allowed in one week."""
    return min(max(base.free_transfers, 0), cfg.max_free) + cfg.max_hits_per_gw


def compare_counts(base: PlanInput, proj: Projection, rules: Rules, cfg: PlannerConfig,
                   min_win: float, min_gain: float, n_worlds: int,
                   seed: int = 0) -> Comparison:  # fmt: skip
    """Solve this week's plan once per transfer count (0 = roll) and choose by the policy."""
    gw0 = base.gws[0]
    plain = replace(base, chips={}, free_hit_values={})
    roll_plan = solve_plan(replace(plain, no_transfer_gws=frozenset({gw0})), cfg)
    rng = np.random.default_rng(seed)
    worlds = rng.choice(proj.points.shape[2], size=min(n_worlds, proj.points.shape[2]),
                        replace=False)  # fmt: skip
    roll_real = chips_mod.score_plan(roll_plan, proj, rules, worlds)
    wk, hz = _xp(roll_plan)
    roll = Option(0, roll_plan, 0, wk, hz, roll_plan.objective, None, 0.0, True, "roll",
                  roll_plan.solve.describe() if roll_plan.solve else "optimal")  # fmt: skip
    options = [roll]
    for k in range(1, max_transfers(base, cfg) + 1):
        try:
            plan = solve_plan(replace(plain, first_transfers=k), cfg)
        except Infeasible:
            options.append(Option(k, None, reason="infeasible (budget, squad or locks)"))
            continue
        diff = chips_mod.score_plan(plan, proj, rules, worlds) - roll_real
        win, gain = float((diff > 0).mean()), float(diff.mean())
        wk, hz = _xp(plan)
        preferred = plan.objective > roll_plan.objective
        robust = win >= min_win or gain >= min_gain
        if not preferred:
            reason = "planner prefers rolling (lower objective)"
        elif not robust:
            reason = (
                f"fails robustness: beats rolling in {win:.0%} of worlds "
                f"(needs {min_win:.0%}) and gains {gain:+.1f} (needs {min_gain:+.1f})"
            )
        else:
            reason = f"passes: beats rolling in {win:.0%} of worlds, {gain:+.1f} pts"
        options.append(Option(k, plan, plan.first.hits, wk, hz, plan.objective, win, gain,
                              preferred and robust, reason,
                              plan.solve.describe() if plan.solve else "optimal"))  # fmt: skip
    passing = [o for o in options[1:] if o.passes]
    chosen = max(passing, key=lambda o: (o.objective, -o.transfers)) if passing else roll
    return Comparison(options, chosen, min_win, min_gain, len(worlds))


__all__ = ["Comparison", "Option", "compare_counts", "max_transfers"]
