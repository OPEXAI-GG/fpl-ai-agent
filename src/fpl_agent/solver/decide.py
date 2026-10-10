"""The weekly decision: squad state + projection -> transfers, chip, XI, captain and a reason.

This module only *recommends*. Whether anything is executed is decided elsewhere
(`fpl_agent.execute`), behind the dry-run default and the safety rails.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Any

import pandas as pd

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
    field_view: list[str] = field(default_factory=list)  # template risk and differentials
    captain_options: list[dict[str, Any]] = field(
        default_factory=list
    )  # top candidates, with upside

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
        if self.captain_options:
            lines.append("  captain options (expected / haul chance): " + "; ".join(
                f"{n(o['player_id'])} {o['xpts']:.1f} / {o['p_haul']:.0%}"
                for o in self.captain_options))  # fmt: skip
        lines.append("  XI: " + ", ".join(n(p) for p in self.xi))
        lines.append("  bench: " + ", ".join(n(p) for p in self.bench))
        for chip, why in self.chip_report.get("held", {}).items():
            lines.append(f"  holding {chip}: {why}")
        lines += [f"  warning: {w}" for w in self.warnings]
        lines += [f"  field: {f}" for f in self.field_view]
        if len(self.horizon) > 1:
            lines.append("plan for the next weeks (1 free transfer a week, re-checked every run):")
            lines += [f"  {self.week_line(w)}" for w in self.horizon[1:]]
        return "\n".join(lines)

    def week_line(self, w: WeekPlan) -> str:
        n = self.name
        moves = ", ".join(f"{n(o)} -> {n(i)}" for i, o in zip(w.transfers_in, w.transfers_out,
                                                              strict=False))  # fmt: skip
        what = f"{len(w.transfers_in)} transfer(s): {moves}" if moves else "roll the transfer"
        extra = (f", hit -{4 * w.hits}" if w.hits else "") + (f", play {w.chip}" if w.chip else "")
        return (f"GW{w.gw}: {what}{extra}; captain {n(w.captain)}; "
                f"expected {w.xp:.1f} pts ({w.ft_before} free)")  # fmt: skip


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


# Rank mode: among XI players whose expected points are within this many points of the best,
# captain the one most likely to haul (10+ points). Costs at most this much expected, buys upside.
RANK_CAPTAIN_SLACK = 0.5
# Until the rank layer weighs ownership properly: an available player owned by at least this
# share of managers is not sold (selling him risks a big rank loss if he hauls) ...
TEMPLATE_KEEP = 40.0
# The report's view of the field: popular players you lack, and your little-owned starters
FIELD_LACK, FIELD_DIFFERENTIAL = 30.0, 5.0
# ... and a player owned by at least this share is captained when within this many expected
# points of the best captain, since most of the field will captain him
TEMPLATE_CAPTAIN, TEMPLATE_CAPTAIN_SLACK = 50.0, 1.0


def rank_risk(gw: int) -> float:
    """How strongly the plan hugs the top managers' effective ownership (PlannerConfig
    risk_lambda): firmly until GW10 while ranks are still settling, lightly after. A rank-target
    mode will later set it from the gap to the target and the gameweeks left."""
    return 0.15 if gw <= 10 else 0.05


def captain_choice(proj: Projection, xi: list[int], slack: float = RANK_CAPTAIN_SLACK,
                   eo: dict[int, float] | None = None, risk: float = 0.0):  # fmt: skip
    """(captain, vice, options): options are the top XI players by expected points this week.
    Among the players within `slack` of the best, the captain is the one most likely to haul;
    with field data (`eo`, `risk`) it is the one with the best expected points less the rank
    risk of the armband (how far double points moves you from the field's effective ownership,
    times his spread and `risk`)."""
    rows = {pid: i for i, pid in enumerate(proj.player_ids)}
    mean = proj.mean()[:, 0]
    sd = proj.points[:, 0, :].std(axis=-1)
    haul = (proj.points[:, 0, :] >= 10).mean(axis=1)
    info = {p: (float(mean[rows[p]]), float(haul[rows[p]])) for p in xi if p in rows}
    ranked = sorted(info, key=lambda p: -info[p][0])
    best = info[ranked[0]][0]
    pool = [p for p in ranked if info[p][0] >= best - slack]
    e = eo or {}

    def armband(p: int) -> float:  # extra rank risk of doubling him rather than not
        x = e.get(p, 0.0)
        return risk * float(sd[rows[p]]) * (abs(2.0 - x) - abs(1.0 - x))

    if risk and e:
        cap = max(pool, key=lambda p: (info[p][0] - armband(p), info[p][1]))
    else:  # no field data: the haul chance decides within the slack
        cap = max(pool, key=lambda p: (info[p][1], info[p][0]))
    vice = next(p for p in ranked if p != cap)
    opts = [{"player_id": p, "xpts": info[p][0], "p_haul": info[p][1]} for p in ranked[:4]]
    return cap, vice, opts


def template_players(proj: Projection, ids, ownership: dict[int, float], floor: float) -> list[int]:
    """Available players among `ids` owned by at least `floor` percent, most owned first."""
    meta = proj.meta.set_index("player_id")
    ok = []
    for p in ids:
        if ownership.get(p, 0.0) < floor or p not in meta.index:
            continue
        r = meta.loc[p]
        chance = 100.0 if pd.isna(r["chance"]) else float(r["chance"])
        if r["status"] == "a" and chance >= 75:
            ok.append(p)
    return sorted(ok, key=lambda p: -ownership[p])


def decide(state: SquadState, proj: Projection, rules: Rules, names: dict[int, str] | None = None,
           cfg: PlannerConfig | None = None, chip_cfg: chips_mod.ChipConfig | None = None,
           use_chips: bool = True,
           ownership: dict[int, float] | None = None,
           eo: dict[int, float] | None = None) -> Decision:  # fmt: skip
    """`state` must already use canonical player ids (see `inputs.to_canonical`). `ownership`
    (percent of managers by player) keeps popular players and favours them as captain; `eo`
    (the field's effective ownership, 1.0 = 100%) is weighed by `cfg.risk_lambda`."""
    own = ownership or {}
    names = names or {}
    cfg = cfg or PlannerConfig()
    cands = prune(build_candidates(proj, state.squad), state.squad)
    locked: frozenset[int] = frozenset()
    if state.wildcard_active and state.base_squad:
        # FPL checks a further Wildcard-week change against both the last deadline's squad and
        # the current one: players sold this week cannot be bought back, nor players bought this
        # week sold again
        now = {p.player_id for p in state.squad}
        was = {p.player_id for p in state.base_squad}
        cands = [c for c in cands if c.player_id not in was - now]
        locked = frozenset(now - was)
    if state.already_transferred and not state.wildcard_active:
        locked = frozenset(p.player_id for p in state.squad)  # this week's moves are done
    keep = template_players(proj, [p.player_id for p in state.squad], own, TEMPLATE_KEEP)
    locked |= frozenset(keep)
    value = sum(p.sell_price for p in state.squad) + state.bank
    base = PlanInput(gws=list(proj.gws), squad=state.squad, bank=state.bank,
                     free_transfers=state.free_transfers, candidates=cands,
                     locked=locked, eo=dict(eo or {}))  # fmt: skip
    avail = state.chips_available(rules) if use_chips else {}
    warnings = []
    if keep:
        warnings.append(
            "kept for ownership (popular players are not sold without your OK): "
            + ", ".join(f"{names.get(p, p)} {own[p]:.0f}%" for p in keep)
        )
    if state.already_transferred:
        warnings.append(
            f"{state.already_transferred} transfer(s) already made for GW{state.next_gw}"
        )
    if state.wildcard_active:  # the Wildcard is already in force: re-plan the whole squad, free
        cfg_wc = replace(cfg, time_limit=chips_mod.ChipConfig().scenario_time_limit)
        chosen = chips_mod.Scenario(
            "wildcard", state.next_gw,
            chips_mod.scenario(base, "wildcard", state.next_gw, cfg_wc, cands, value),
        )  # fmt: skip
        report = {"held": {}, "play_now": "wildcard", "scenarios": [],
                  "note": "wildcard already active: free changes until the deadline"}  # fmt: skip
    elif avail:
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
    options: list[dict[str, Any]] = []
    if wk.chip != "3xc" and wk.xi and set(wk.xi) <= set(proj.player_ids):
        wk.captain, wk.vice, options = captain_choice(proj, wk.xi, eo=eo, risk=cfg.risk_lambda)
        top = template_players(proj, wk.xi, own, TEMPLATE_CAPTAIN)
        if top and top[0] != wk.captain:
            mean = dict(zip(proj.player_ids, proj.mean()[:, 0], strict=True))
            if mean[top[0]] >= mean[wk.captain] - TEMPLATE_CAPTAIN_SLACK:
                wk.captain, wk.vice = top[0], wk.captain
            else:
                warnings.append(f"{names.get(top[0], top[0])} ({own[top[0]]:.0f}% owned) is not "
                                "captain: most of the field will captain him")  # fmt: skip
    view = []
    if own and wk.squad:
        have = set(wk.squad)
        lack = template_players(proj, [p for p in proj.player_ids if p not in have], own,
                                FIELD_LACK)[:3]  # fmt: skip
        diff = sorted((p for p in wk.xi if own.get(p, 0.0) < FIELD_DIFFERENTIAL),
                      key=lambda p: own.get(p, 0.0))[:5]  # fmt: skip
        if lack:
            view.append(
                "popular players you don't own (risk if they haul): "
                + ", ".join(f"{names.get(p, p)} {own[p]:.0f}%" for p in lack)
            )
        if diff:
            view.append(
                f"your differentials (under {FIELD_DIFFERENTIAL:.0f}% owned, upside): "
                + ", ".join(f"{names.get(p, p)} {own.get(p, 0.0):.0f}%" for p in diff)
            )
    return Decision(
        gw=wk.gw, week=wk, chip=wk.chip, transfers_in=wk.transfers_in,
        transfers_out=wk.transfers_out, hits=wk.hits, captain=wk.captain, vice=wk.vice,
        xi=wk.xi, bench=wk.bench, xp=wk.xp,
        xp_no_transfers=_best_xi_points(proj, [p.player_id for p in state.squad], 0),
        bank_after=_bank_after(state, wk, cands),
        ft_after=(
            chosen.plan.weeks[1].ft_before if len(chosen.plan.weeks) > 1 else chosen.plan.ft_end
        ),
        names=names, chip_report=report, horizon=chosen.plan.weeks, warnings=warnings,
        captain_options=options, field_view=view,
    )  # fmt: skip


def _bank_after(state: SquadState, wk: WeekPlan, cands) -> int:
    sell = {p.player_id: p.sell_price for p in state.squad}
    price = {c.player_id: c.price for c in cands}
    return (
        state.bank + sum(sell[o] for o in wk.transfers_out) - sum(price[i] for i in wk.transfers_in)
    )
