"""The weekly decision: squad state + projection -> transfers, chip, XI, captain and a reason.

This module only *recommends*. Whether anything is executed is decided elsewhere
(`fpl_agent.execute`), behind the dry-run default and the safety rails.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from fpl_agent.data.entry import SquadState
from fpl_agent.model.project import Projection
from fpl_agent.rules import Rules
from fpl_agent.solver import chips as chips_mod
from fpl_agent.solver.inputs import build_candidates, prune
from fpl_agent.solver.planner import PlanInput, PlannerConfig, WeekPlan


@dataclass
class Decision:
    gw: int
    week: WeekPlan  # what to do this gameweek
    chip: str | None
    transfers_in: list[int]
    transfers_out: list[int]
    hits: int
    captain: int
    vice: int
    xi: list[int]
    bench: list[int]
    xp: float
    xp_no_transfers: float  # same squad, best XI, no moves
    bank_after: int
    ft_after: int
    names: dict[int, str] = field(default_factory=dict)
    chip_report: dict[str, Any] = field(default_factory=dict)
    horizon: list[WeekPlan] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def action(self) -> str:
        if self.chip:
            return f"play {self.chip}"
        return "make transfers" if self.transfers_in else "roll the free transfer"

    def name(self, pid: int) -> str:
        return self.names.get(pid, str(pid))

    def summary(self) -> str:
        n = self.name
        lines = [f"GW{self.gw}: {self.action}  (expected {self.xp:.1f} pts)"]
        for i, o in zip(self.transfers_in, self.transfers_out, strict=False):
            lines.append(f"  transfer: {n(o)} -> {n(i)}")
        if self.hits:
            lines.append(f"  hit: -{4 * self.hits}")
        lines.append(f"  captain {n(self.captain)}, vice {n(self.vice)}")
        lines.append("  XI: " + ", ".join(n(p) for p in self.xi))
        lines.append("  bench: " + ", ".join(n(p) for p in self.bench))
        for chip, why in self.chip_report.get("held", {}).items():
            lines.append(f"  holding {chip}: {why}")
        lines += [f"  warning: {w}" for w in self.warnings]
        return "\n".join(lines)


def _best_xi_points(proj: Projection, squad_ids: list[int], g: int) -> float:
    """Expected points of the best XI/captain from a fixed squad (the 'do nothing' benchmark)."""
    from fpl_agent.solver.planner import FORMATION_MAX, FORMATION_MIN

    mean = proj.mean()
    pos = dict(zip(proj.meta["player_id"], proj.meta["position"], strict=True))
    rows = {pid: i for i, pid in enumerate(proj.player_ids)}
    xp = {p: float(mean[rows[p], g]) if p in rows else 0.0 for p in squad_ids}
    chosen: list[int] = []
    for p in ("GKP", "DEF", "MID", "FWD"):
        mine = sorted((i for i in squad_ids if pos.get(i) == p), key=lambda i: -xp[i])
        chosen += mine[: FORMATION_MIN[p]]
    rest = sorted((i for i in squad_ids if i not in chosen and pos.get(i) != "GKP"),
                  key=lambda i: -xp[i])  # fmt: skip
    for i in rest:
        if len(chosen) == 11:
            break
        if sum(1 for c in chosen if pos[c] == pos[i]) < FORMATION_MAX[pos[i]]:
            chosen.append(i)
    return sum(xp[i] for i in chosen) + max(xp[i] for i in chosen)


def decide(state: SquadState, proj: Projection, rules: Rules, names: dict[int, str] | None = None,
           cfg: PlannerConfig | None = None, chip_cfg: chips_mod.ChipConfig | None = None,
           use_chips: bool = True) -> Decision:  # fmt: skip
    """`state` must already use canonical player ids (see `inputs.to_canonical`)."""
    cfg = cfg or PlannerConfig()
    cands = prune(build_candidates(proj, state.squad), state.squad)
    value = sum(p.sell_price for p in state.squad) + state.bank
    base = PlanInput(gws=list(proj.gws), squad=state.squad, bank=state.bank,
                     free_transfers=state.free_transfers, candidates=cands)  # fmt: skip
    avail = state.chips_available(rules) if use_chips else {}
    warnings = []
    if state.already_transferred:
        warnings.append(
            f"{state.already_transferred} transfer(s) already made for GW{state.next_gw}"
        )
    if avail:
        cd = chips_mod.search(base, proj, rules, avail, value, cfg, chip_cfg)
        chosen, report = cd.chosen, {
            "held": cd.held, "play_now": cd.play_now,
            "scenarios": [{"chip": s.chip, "gw": s.gw, "gain": round(s.gain, 2),
                           "win_share": round(s.win_share, 2), "ok": s.ok} for s in cd.scenarios],
        }  # fmt: skip
    else:
        from fpl_agent.solver.planner import solve_plan

        chosen, report = (
            chips_mod.Scenario(None, None, solve_plan(base, cfg)),
            {"held": {}, "play_now": None, "scenarios": []},
        )
    wk = chosen.plan.first
    return Decision(
        gw=wk.gw, week=wk, chip=wk.chip, transfers_in=wk.transfers_in,
        transfers_out=wk.transfers_out, hits=wk.hits, captain=wk.captain, vice=wk.vice,
        xi=wk.xi, bench=wk.bench, xp=wk.xp,
        xp_no_transfers=_best_xi_points(proj, [p.player_id for p in state.squad], 0),
        bank_after=_bank_after(state, wk, cands),
        ft_after=(
            chosen.plan.weeks[1].ft_before if len(chosen.plan.weeks) > 1 else chosen.plan.ft_end
        ),
        names=names or {}, chip_report=report, horizon=chosen.plan.weeks, warnings=warnings,
    )  # fmt: skip


def _bank_after(state: SquadState, wk: WeekPlan, cands) -> int:
    sell = {p.player_id: p.sell_price for p in state.squad}
    price = {c.player_id: c.price for c in cands}
    return (
        state.bank + sum(sell[o] for o in wk.transfers_out) - sum(price[i] for i in wk.transfers_in)
    )
