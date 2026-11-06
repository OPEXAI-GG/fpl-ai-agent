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
from fpl_agent.solver import rank_target
from fpl_agent.solver.inputs import MIN_CHANCE, build_candidates, prune, unstartable
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
    checks: list[str] = field(default_factory=list)  # robustness checks that passed
    captain_options: list[dict[str, Any]] = field(
        default_factory=list
    )  # top candidates, with upside
    objective: float | None = None  # the planner's full objective of the followed plan
    solve: str = "optimal"  # how the followed plan's solve ended
    comparison: list[dict[str, Any]] = field(default_factory=list)  # one row per transfer count
    comparison_note: str = ""  # why no comparison was made, when it was not

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
        lines += [f"  check: {c}" for c in self.checks]
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


# A week's transfers are made only if, across simulated worlds, they beat rolling the transfer
# in at least ROBUST_MIN_WIN of them, or gain ROBUST_MIN_GAIN points on average over the horizon
ROBUST_MIN_WIN, ROBUST_MIN_GAIN, ROBUST_WORLDS = 0.55, 1.0, 300


def _moves(plan, names: dict[int, str]) -> str:
    first = plan.first
    pairs = zip(first.transfers_in, first.transfers_out, strict=False)
    return ", ".join(f"{names.get(o, o)} -> {names.get(i, i)}" for i, o in pairs)


def _compare_transfers(chosen, base: PlanInput, proj: Projection, rules: Rules,
                       cfg: PlannerConfig, names: dict[int, str], warnings: list[str],
                       checks: list[str], min_win: float | None = None):  # fmt: skip
    """Every transfer count this week (0 = roll .. the planner's maximum) solved with the same
    inputs and scored against rolling in the same worlds; the best that passes the policy is
    followed, else the transfer is rolled (see `compare.py`). Returns (scenario, comparison)."""
    from fpl_agent.solver.compare import compare_counts

    need = ROBUST_MIN_WIN if min_win is None else min_win
    cmp = compare_counts(base, proj, rules, cfg, need, ROBUST_MIN_GAIN, ROBUST_WORLDS)
    preferred = len(chosen.plan.first.transfers_in)
    pref = next((o for o in cmp.options if o.transfers == preferred), None)
    if preferred and pref is not None and not pref.passes and pref.plan is not None:
        warnings.append(f"held {_moves(pref.plan, names)}: it beat rolling the transfer in only "
                        f"{pref.win_share:.0%} of simulated worlds ({pref.gain:+.1f} pts over the "
                        "horizon)")  # fmt: skip
    pick = cmp.chosen
    if pick.transfers:
        checks.append(f"{_moves(pick.plan, names)} beat rolling the transfer in "
                      f"{pick.win_share:.0%} of simulated worlds ({pick.gain:+.1f} pts over the "
                      "horizon)")  # fmt: skip
        if preferred and pick.transfers != preferred:
            warnings.append(
                f"chose {pick.transfers} transfer(s) instead of the planner's preferred "
                f"{preferred}: the best option that passes the policy"
            )
    return chips_mod.Scenario(chosen.chip, chosen.gw, pick.plan), cmp


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
           eo: dict[int, float] | None = None,
           chasing: rank_target.Chase | None = None,
           min_win: float | None = None) -> Decision:  # fmt: skip
    """`state` must already use canonical player ids (see `inputs.to_canonical`). `ownership`
    (percent of managers by player) keeps popular players and favours them as captain; `eo`
    (the field's effective ownership, 1.0 = 100%) is weighed by `cfg.risk_lambda`. `chasing`
    (behind a rank target) moves the catch-up risk onto the armband: the least-captained player
    within its slack of the best captain, and no switch to the most-owned one. `min_win` is the
    share of simulated worlds this week's moves must beat rolling in (default ROBUST_MIN_WIN;
    the memory lowers it)."""
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
    ft = state.free_transfers  # the bank at the start of the week
    further = state.remaining_free_transfers is not None  # dry analysis of more moves
    if state.already_transferred and not state.wildcard_active and further:
        # respect the completed moves: keep this week's buys, never re-buy this week's sales
        now = {p.player_id for p in state.squad}
        was = {p.player_id for p in state.base_squad} or now
        cands = [c for c in cands if c.player_id not in was - now]
        locked = frozenset(now - was)
        ft = max(0, int(state.remaining_free_transfers))  # confirmed: not reduced again
    elif state.already_transferred and not state.wildcard_active:
        locked = frozenset(p.player_id for p in state.squad)  # this week's moves are done
        ft = max(0, ft - state.already_transferred)  # they used the bank; next week adds one
    # the field's core stays for the whole plan, not just this week: selling a player nearly
    # every top manager owns is a bet against the field the catch-up does not need. Checked
    # again every run, so an injured or benched one can still go.
    keep = template_players(proj, [p.player_id for p in state.squad], own, TEMPLATE_KEEP)
    value = sum(p.sell_price for p in state.squad) + state.bank
    # the execution rails' availability policy, applied to this week's XI by the planner itself
    benched = unstartable(proj, [c.player_id for c in cands])
    base = PlanInput(gws=list(proj.gws), squad=state.squad, bank=state.bank,
                     free_transfers=ft, candidates=cands,
                     locked=locked, kept=frozenset(keep), eo=dict(eo or {}),
                     no_start=benched)  # fmt: skip
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
        if further and not state.wildcard_active:
            warnings.append(
                f"dry analysis: further moves compared with {ft} free transfer(s) left as "
                "confirmed; recommendation only, execution keeps a hand-edited team locked"
            )

    def choose(pi: PlanInput):
        if state.wildcard_active:  # Wildcard already in force: re-plan the whole squad, free
            cfg_wc = replace(cfg, time_limit=chips_mod.ChipConfig().scenario_time_limit)
            chosen = chips_mod.Scenario(
                "wildcard", state.next_gw,
                chips_mod.scenario(pi, "wildcard", state.next_gw, cfg_wc, cands, value),
            )  # fmt: skip
            note = "wildcard already active: free changes until the deadline"
            report = {"held": {}, "play_now": "wildcard", "scenarios": [], "note": note}
        elif avail:
            cd = chips_mod.search(pi, proj, rules, avail, value, cfg, chip_cfg)
            chosen, report = cd.chosen, {
                "held": cd.held, "play_now": cd.play_now,
                "scenarios": [{"chip": s.chip, "gw": s.gw, "gain": round(s.gain, 2),
                               "win_share": round(s.win_share, 2), "ok": s.ok,
                               "optimal": s.optimal,
                               "max_gain": None if s.max_gain is None else round(s.max_gain, 2)}
                              for s in cd.scenarios],
                "baseline_solve": (cd.baseline.plan.solve.describe()
                                   if cd.baseline.plan.solve else "optimal"),
            }  # fmt: skip
        else:
            from fpl_agent.solver.planner import solve_plan

            chosen, report = (
                chips_mod.Scenario(None, None, solve_plan(pi, cfg)),
                {"held": {}, "play_now": None, "scenarios": []},
            )
        return chosen, report

    from fpl_agent.solver.mip import Infeasible

    try:
        chosen, report = choose(base)
    except Infeasible:
        if not base.no_start:
            raise
        base = replace(base, no_start=frozenset())  # no legal XI without them: say so
        chosen, report = choose(base)
        warnings.append("no legal XI avoids unavailable/doubtful players this week: the lineup "
                        "below starts one and execution would block it")  # fmt: skip
    owned_out = sorted(p.player_id for p in state.squad if p.player_id in base.no_start)
    if owned_out:
        warnings.append("not started under the availability rule (status not 'a' or chance "
                        f"below {MIN_CHANCE}%): "
                        + ", ".join(names.get(p, str(p)) for p in owned_out))  # fmt: skip
    checks: list[str] = []
    comparison = None
    locked_all = bool(state.already_transferred) and not state.wildcard_active and not further
    if (
        not state.wildcard_active and not locked_all and not any(w.chip for w in chosen.plan.weeks)
    ):  # a chip plan scores differently; the chip search already weighed its worlds
        chosen, comparison = _compare_transfers(chosen, base, proj, rules, cfg, names, warnings,
                                                checks, min_win)  # fmt: skip
    wk = chosen.plan.first
    options: list[dict[str, Any]] = []
    if wk.chip != "3xc" and wk.xi and set(wk.xi) <= set(proj.player_ids):
        chase = chasing is not None and chasing.behind
        if chase:
            wk.captain, wk.vice, options = captain_choice(
                proj,
                wk.xi,
                slack=max(RANK_CAPTAIN_SLACK, chasing.captain_slack),
                eo=eo,
                risk=rank_target.CHASE_RISK,
            )
        else:
            wk.captain, wk.vice, options = captain_choice(proj, wk.xi, eo=eo, risk=cfg.risk_lambda)
        top = [] if chase else template_players(proj, wk.xi, own, TEMPLATE_CAPTAIN)
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
        captain_options=options, field_view=view, checks=checks,
        objective=chosen.plan.objective,
        solve=chosen.plan.solve.describe() if chosen.plan.solve else "optimal",
        comparison=comparison.rows() if comparison else [],
        comparison_note="" if comparison else (
            "wildcard active: the whole squad is re-planned" if state.wildcard_active else
            f"{state.already_transferred} transfer(s) already made: squad locked this week"
            if locked_all else "the plan plays a chip: the chip search weighed it instead"),
    )  # fmt: skip


def _bank_after(state: SquadState, wk: WeekPlan, cands) -> int:
    sell = {p.player_id: p.sell_price for p in state.squad}
    price = {c.player_id: c.price for c in cands}
    return (
        state.bank + sum(sell[o] for o in wk.transfers_out) - sum(price[i] for i in wk.transfers_in)
    )
