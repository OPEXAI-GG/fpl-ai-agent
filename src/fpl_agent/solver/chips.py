"""Chip scenario search with sensitivity gates.

A chip is a one-off, so the question is *when*, not just *whether*: we solve one plan per
(chip, gameweek) scenario plus a no-chip baseline, then judge each scenario twice:

* on expected points (planner objective, which includes the future value of the squad), and
* on the sampled worlds: how often does the chip plan actually score more than the baseline?
  A Wildcard must win at least `wildcard_win_share` of the worlds, so it is never played on the
  strength of an average that a handful of lucky outcomes produced.

A chip is played now only if now is the best moment (no later gameweek in the horizon is better by
more than `hold_margin`) and the gain clears the chip's hold threshold. A window that closes
inside the horizon makes the chip must-use: the best remaining moment is taken even below the
threshold, because an unused chip is worth nothing.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace

import numpy as np

from fpl_agent.model.project import Projection
from fpl_agent.rules import Rules
from fpl_agent.rules.engine import auto_sub, effective_captain
from fpl_agent.solver.planner import (
    Candidate,
    Plan,
    PlannerConfig,
    PlanInput,
    WeekPlan,
    free_hit_team,
    solve_plan,
)

MIN_GAIN = {"wildcard": 6.0, "freehit": 8.0, "bboost": 8.0, "3xc": 4.0}  # expected points over baseline
MIN_WIN = {"wildcard": 0.70, "freehit": 0.55, "bboost": 0.55, "3xc": 0.50}


@dataclass(frozen=True)
class ChipConfig:
    hold_margin: float = 1.0  # a later week must beat now by this much to make us wait
    n_worlds: int = 300
    min_gain: dict = field(default_factory=lambda: dict(MIN_GAIN))
    min_win: dict = field(default_factory=lambda: dict(MIN_WIN))
    scenario_time_limit: float = 20.0


@dataclass
class Scenario:
    chip: str | None
    gw: int | None
    plan: Plan
    gain: float = 0.0  # planner objective minus the baseline's
    win_share: float = 1.0  # fraction of sampled worlds where it beats the baseline
    realized_gain: float = 0.0  # mean sampled-world difference
    ok: bool = True
    note: str = ""


@dataclass
class ChipDecision:
    baseline: Scenario
    scenarios: list[Scenario]
    chosen: Scenario  # the plan to follow (baseline when no chip is played now)
    play_now: str | None
    held: dict[str, str]  # chip -> why it was kept


def score_plan(plan: Plan, proj: Projection, rules: Rules, worlds: np.ndarray) -> np.ndarray:
    """Realised points of a plan in each sampled world (auto-subs, captain cover, hits)."""
    pos = dict(zip(proj.meta["player_id"], proj.meta["position"], strict=True))
    rows = {pid: i for i, pid in enumerate(proj.player_ids)}
    total = np.zeros(len(worlds))
    gidx = {gw: i for i, gw in enumerate(proj.gws)}
    for wk in plan.weeks:
        g = gidx[wk.gw]
        for n, s in enumerate(worlds):
            pts = {pid: float(proj.points[rows[pid], g, s]) for pid in wk.squad if pid in rows}
            mins = {pid: int(proj.minutes[rows[pid], g, s]) for pid in wk.squad if pid in rows}
            final = auto_sub(wk.xi, wk.bench, mins, pos, rules)
            cap = effective_captain(wk.captain, wk.vice, mins)
            mult = 3 if wk.chip == "3xc" else 2
            v = sum(pts.get(p, 0.0) for p in final)
            if cap is not None:
                v += (mult - 1) * pts.get(cap, 0.0)
            if wk.chip == "bboost":
                v += sum(pts.get(p, 0.0) for p in wk.squad if p not in final)
            total[n] += v - 4 * wk.hits
    return total


def _plan_input(base: PlanInput, chips: dict[int, str]) -> PlanInput:
    return replace(base, chips=chips)


def scenario(base: PlanInput, chip: str, gw: int, cfg: PlannerConfig,
             cands: list[Candidate], squad_value: int) -> Plan:  # fmt: skip
    pi = _plan_input(base, {gw: chip})
    if chip == "freehit":
        t = base.gws.index(gw)
        val, wk = free_hit_team(cands, t, gw, squad_value, cfg)
        pi = replace(pi, free_hit_values={gw: val})
        plan = solve_plan(pi, cfg)
        plan.weeks[t] = wk
        return plan
    return solve_plan(pi, cfg)


def search(base: PlanInput, proj: Projection, rules: Rules, available: dict[str, tuple[int, int]],
           squad_value: int, cfg: PlannerConfig | None = None,
           chip_cfg: ChipConfig | None = None) -> ChipDecision:  # fmt: skip
    cfg = cfg or PlannerConfig()
    cc = chip_cfg or ChipConfig()
    scfg = replace(cfg, time_limit=cc.scenario_time_limit)
    rng = np.random.default_rng(0)
    worlds = rng.choice(proj.points.shape[2], size=min(cc.n_worlds, proj.points.shape[2]),
                        replace=False)  # fmt: skip
    base_plan = solve_plan(replace(base, chips={}), cfg)
    base_real = score_plan(base_plan, proj, rules, worlds)
    baseline = Scenario(None, None, base_plan)
    out: list[Scenario] = []
    for chip, (lo, hi) in available.items():
        for gw in base.gws:
            if not lo <= gw <= hi:
                continue
            try:
                plan = scenario(base, chip, gw, scfg, base.candidates, squad_value)
            except Exception as exc:  # noqa: BLE001 - an infeasible scenario is just not an option
                out.append(Scenario(chip, gw, base_plan, ok=False, note=f"unsolved: {exc}"))
                continue
            real = score_plan(plan, proj, rules, worlds)
            sc = Scenario(chip, gw, plan, gain=plan.objective - base_plan.objective,
                          win_share=float((real > base_real).mean()),
                          realized_gain=float((real - base_real).mean()))  # fmt: skip
            out.append(sc)
    return _decide(base, baseline, out, available, cc)


def _decide(base: PlanInput, baseline: Scenario, scenarios: list[Scenario],
            available: dict[str, tuple[int, int]], cc: ChipConfig) -> ChipDecision:  # fmt: skip
    held: dict[str, str] = {}
    now_gw = base.gws[0]
    horizon_end = base.gws[-1]
    best_now: Scenario | None = None
    for chip, (_, hi) in available.items():
        mine = [s for s in scenarios if s.chip == chip and s.ok]
        if not mine:
            held[chip] = "no playable scenario in the horizon"
            continue
        must_use = hi <= horizon_end
        best = max(mine, key=lambda s: s.gain)
        now = next((s for s in mine if s.gw == now_gw), None)
        need = 0.0 if must_use else cc.min_gain[chip]
        if best.win_share < cc.min_win[chip] and not must_use:
            held[chip] = f"won only {best.win_share:.0%} of simulated worlds"
        elif best.gain < need:
            held[chip] = f"best gain {best.gain:+.1f} below the {need:.0f}-point threshold"
        elif now is None or (best.gw != now_gw and best.gain - now.gain > cc.hold_margin):
            held[chip] = f"better in GW{best.gw} ({best.gain:+.1f})"
        elif now.win_share < cc.min_win[chip] and not must_use:
            held[chip] = f"won only {now.win_share:.0%} of simulated worlds now"
        elif now.gain < need:
            held[chip] = f"gain now {now.gain:+.1f} below the {need:.0f}-point threshold"
        elif best_now is None or now.gain > best_now.gain:
            best_now = now
    if best_now is None:
        return ChipDecision(baseline, scenarios, baseline, None, held)
    for s in scenarios:
        if s is not best_now and s.chip and s.gw == now_gw:
            held.setdefault(s.chip, "another chip is better this week")
    return ChipDecision(baseline, scenarios, best_now, best_now.chip, held)
