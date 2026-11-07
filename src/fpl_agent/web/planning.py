"""One manager's recommendation, from public data and the shared forecast.

The engine does all the football and optimisation work (`team.current_team`, `solver.decide`,
`execution.safety`); this module only decides *which* questions to ask it and how to report
the answers honestly:

* The squad is the last deadline's public picks plus any public transfers. Transfers made for
  the coming gameweek are not public until its deadline, so the recommendation always states
  that assumption.
* FPL never publishes the free-transfer count. Every plausible balance from
  `CurrentTeam.ft_scenarios()` (including zero when this week's transfers are not confirmed)
  is planned with the same forecast, configuration and sampled worlds. When the advice differs
  between balances it is reported as conditional, never as one unconditional move.
* Each plan is checked by the execution rails in dry mode; a lineup the rails would refuse is
  never shown as the recommended lineup.
* Strategy changes only how the plan treats other managers' ownership, never the forecast.
"""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from fpl_agent.config import Settings
from fpl_agent.data.calendar import next_event
from fpl_agent.data.schemas import Bootstrap
from fpl_agent.execution import safety
from fpl_agent.rules import Rules
from fpl_agent.solver import rank_target
from fpl_agent.solver.decide import Decision, decide, rank_risk
from fpl_agent.solver.inputs import MIN_CHANCE, to_canonical
from fpl_agent.solver.planner import PlannerConfig
from fpl_agent.team import current_team, public_view
from fpl_agent.team.merge import CONFIRMED, CONFLICTING, INFERRED, UNKNOWN, CurrentTeam, to_state
from fpl_agent.team.report import action_key
from fpl_agent.web.forecasts import Shared
from fpl_agent.web.provider import Fetched, Gateway, ProviderUnavailable

PLAN_VERSION = "web-plan-1"
STRATEGIES = {
    "points": {
        "label": "Points-focused",
        "summary": "Maximises expected points and ignores what other managers own.",
        "effect": "No popular player is protected from a sale and the captain is chosen on "
        "expected points and haul chance alone.",
    },
    "balanced": {
        "label": "Balanced",
        "summary": "Expected points, with protection against big rank swings.",
        "effect": "Very popular players (40%+ owned) are kept unless unavailable, a widely "
        "owned captain is preferred when close, and the plan leans slightly toward what top "
        "managers own. This is the weekly agent's own setting.",
    },
    "rank": {
        "label": "Rank-chasing",
        "summary": "Takes calculated captaincy risk to close the gap to a target rank.",
        "effect": "Keeps the balanced squad rules, but while you are behind the target the "
        "captain is the least-captained option within a small expected-points margin of the "
        "best. Ahead of the target it plays like balanced.",
    },
}
TARGETS = (10_000, 100_000, 1_000_000)
DEFAULT_STRATEGY, DEFAULT_TARGET = "balanced", 100_000
TIME_BUDGET = 900.0  # seconds a plan job may run before it stops with a timed-out result


class PlanUnavailable(RuntimeError):
    """The plan cannot be made from what is available (message is user-facing)."""


@dataclass
class PublicState:
    entry_id: int
    gw: int  # the gameweek being planned (FPL's next)
    bootstrap: dict
    entry: dict
    history: dict
    picks: dict
    transfers: list
    team: CurrentTeam
    sources: dict[str, dict]  # endpoint -> retrieval metadata
    now: datetime


def load_state(
    gw: Gateway, entry_id: int, rules: Rules, season: int, force: bool = False
) -> PublicState:
    """Everything public about the entry that the plan and the dashboard need."""
    b = gw.bootstrap()
    ev = next_event(Bootstrap.model_validate(b.body))
    if ev is None:
        raise PlanUnavailable("FPL lists no upcoming gameweek: the season may be over.")
    e = gw.entry(entry_id, force=force)
    h = gw.history(entry_id, force=force)
    prev = ev.id - 1
    if prev >= 1:
        prev_ev = next((x for x in b.body["events"] if x["id"] == prev), {})
        p = gw.picks(entry_id, prev, final=bool(prev_ev.get("finished")), force=force)
    else:
        p = Fetched({"picks": [], "entry_history": {}}, time.time())
    t = gw.transfers(entry_id, force=force)
    now = datetime.now(UTC)
    if not p.body.get("picks"):
        raise PlanUnavailable(
            f"This team has no public squad for GW{prev}: it may have joined after that "
            "deadline. A plan is possible once its first squad is public."
        )
    pv = public_view(entry_id, ev.id, p.body, t.body, h.body, b.body, rules, now)
    team = current_team(
        entry=entry_id,
        season=season,
        gameweek=ev.id,
        now=now,
        rules=rules,
        bootstrap=b.body,
        public=pv,
        root=None,
    )
    sources = {
        "bootstrap": b.meta(),
        "entry": e.meta(),
        "history": h.meta(),
        "picks": p.meta(),
        "transfers": t.meta(),
    }
    return PublicState(entry_id, ev.id, b.body, e.body, h.body, p.body, t.body, team, sources, now)


def fingerprint(team: CurrentTeam) -> str:
    """The parts of the squad state a plan depends on (not when they were read)."""
    d = team.as_dict()
    keep = {
        "squad": d["squad"]["value"],
        "squad_status": d["squad"]["status"],
        "bank": d["bank"]["value"],
        "ft": d["free_transfers"]["value"],
        "ft_status": d["free_transfers"]["status"],
        "made": d["transfers_made"]["value"],
        "chips": {k: v["value"] for k, v in d["chips"].items()},
        "selling": {k: v["value"] for k, v in d["selling"].items()},
        "deadline_squad": d["deadline_squad"],
        "gw": d["gameweek"],
        "scenarios": team.ft_scenarios(),
    }
    return hashlib.sha256(json.dumps(keep, sort_keys=True).encode()).hexdigest()[:16]


def prefs_of(prefs: dict | None) -> dict:
    p = prefs or {}
    strategy = p.get("strategy") if p.get("strategy") in STRATEGIES else DEFAULT_STRATEGY
    target = int(p.get("target_rank") or DEFAULT_TARGET)
    if target not in TARGETS:
        target = DEFAULT_TARGET
    return {"strategy": strategy, "target_rank": target}


def plan_key(forecast_key: str, team_fp: str, prefs: dict) -> str:
    p = prefs_of(prefs)
    blob = json.dumps(
        [
            PLAN_VERSION,
            forecast_key,
            team_fp,
            p["strategy"],
            p["target_rank"] if p["strategy"] == "rank" else None,
        ]
    )
    return "plan-" + hashlib.sha256(blob.encode()).hexdigest()[:20]


# ---- running the engine ------------------------------------------------------------------------
def _chase(gw: Gateway, history: dict, next_gw: int, target: int):
    class _Standings:  # rank_target reads standings through this one method
        def league_standings_raw(self, league_id: int, page: int = 1):
            return gw.standings_page(page).body

    try:
        return rank_target.chase(_Standings(), history, next_gw, target), None
    except (ProviderUnavailable, Exception) as exc:  # noqa: BLE001  (plan without the target)
        return None, f"rank target could not be read ({type(exc).__name__}): planned as balanced"


def strategy_inputs(shared: Shared, gw_id: int, prefs: dict, gateway: Gateway, history: dict):
    """(ownership, eo, PlannerConfig, chase, notes) for the strategy. The forecast is the same
    for every strategy; only the treatment of other managers' ownership changes."""
    p = prefs_of(prefs)
    notes: list[str] = []
    if p["strategy"] == "points":
        return {}, {}, PlannerConfig(risk_lambda=0.0), None, notes
    eo = shared.eo
    cfg = PlannerConfig(risk_lambda=rank_risk(gw_id) if eo else 0.0)
    if not eo:
        notes.append(
            "no top-10k ownership sample in this data revision: popular-player rules "
            "use FPL's overall ownership"
        )
    chase = None
    if p["strategy"] == "rank":
        chase, why = _chase(gateway, history, gw_id, p["target_rank"])
        if why:
            notes.append(why)
        elif chase is not None:
            notes.append(chase.note())
    return shared.ownership, eo, cfg, chase, notes


def plan_one(
    shared: Shared, state: PublicState, rules: Rules, ft: int, inputs, chips: bool
) -> Decision:
    own, eo, cfg, chase, _ = inputs
    st = to_canonical(to_state(state.team, state.bootstrap, rules, ft), shared.pool)
    return decide(
        st,
        shared.proj,
        rules,
        names=shared.names,
        cfg=cfg,
        use_chips=chips,
        ownership=own,
        eo=eo,
        chasing=chase,
        adaptive=True,
    )


def rails(shared: Shared, state: PublicState, rules: Rules, ft: int, d: Decision) -> dict:
    """The execution rails' verdict on the plan, in dry mode (nothing is sent anywhere)."""
    st = to_canonical(to_state(state.team, state.bootstrap, rules, ft), shared.pool)
    mean = dict(zip(shared.proj.player_ids, shared.proj.mean()[:, 0].astype(float), strict=True))
    recent = {
        r["event"]: r.get("event_transfers_cost", 0) // 4 for r in state.history.get("current", [])
    }
    pool = shared.pool[["player_id", "team_id", "position", "status", "chance"]]
    ctx = safety.Context(
        Settings(mode="dry"),
        st,
        pool,
        mean,
        bootstrap_next_gw=state.gw,
        recent_hits=recent,
        projected_share=len(shared.proj.meta) / max(len(pool), 1),
    )
    v = safety.check(d, ctx, rules)
    return {
        "ok": v.ok,
        "lineup_blocked": v.blocked("lineup"),
        "transfers_blocked": v.blocked("transfers"),
        "violations": [str(x) for x in v.violations],
    }


def run(
    shared: Shared,
    state: PublicState,
    rules: Rules,
    prefs: dict,
    gateway: Gateway,
    on_stage=None,
    budget: float = TIME_BUDGET,
) -> dict:
    """Plan every free-transfer balance; returns the final payload. `on_stage(stage, progress,
    payload)` receives the preliminary payload as soon as the first balance is planned."""
    if shared.proj.gws[0] != state.gw:
        raise PlanUnavailable(
            f"The forecast is for GW{shared.proj.gws[0]} but FPL's next gameweek is GW{state.gw}. "
            "A new forecast is needed before a plan can be made."
        )
    if not state.team.usable:
        raise PlanUnavailable("The public data does not give a usable squad for this team.")
    t0 = time.time()
    inputs = strategy_inputs(shared, state.gw, prefs, gateway, state.history)
    fts = state.team.ft_scenarios()
    chips_possible = bool(
        to_state(state.team, state.bootstrap, rules, fts[0]).chips_available(rules)
    )
    timings: dict[str, float] = {}
    plans: dict[int, Decision] = {}
    verdicts: dict[int, dict] = {}

    def emit(stage: str, progress: float, final: bool, timed_out: bool = False) -> dict:
        body = payload(
            shared,
            state,
            rules,
            prefs,
            plans,
            verdicts,
            fts,
            inputs,
            final,
            timings,
            timed_out,
            chips_possible,
        )
        if on_stage:
            on_stage(stage, progress, body)
        return body

    # 1. the most likely balance, without the chip search: quick, labelled preliminary
    t = time.time()
    plans[fts[0]] = plan_one(shared, state, rules, fts[0], inputs, chips=False)
    verdicts[fts[0]] = rails(shared, state, rules, fts[0], plans[fts[0]])
    timings["primary_seconds"] = round(time.time() - t, 1)
    body = emit("planned the most likely free-transfer balance", 0.3, final=False)
    # 2. every other plausible balance, same forecast, thresholds and worlds
    for i, ft in enumerate(fts[1:], start=1):
        if time.time() - t0 > budget:
            return emit(
                "stopped: time budget used", body.get("progress", 0.5), final=False, timed_out=True
            )
        t = time.time()
        plans[ft] = plan_one(shared, state, rules, ft, inputs, chips=False)
        verdicts[ft] = rails(shared, state, rules, ft, plans[ft])
        timings[f"ft{ft}_seconds"] = round(time.time() - t, 1)
        body = emit(
            f"planned {ft} free transfer(s)", 0.3 + 0.3 * i / max(len(fts) - 1, 1), final=False
        )
    # 3. the chip search on the most likely balance
    if chips_possible:
        if time.time() - t0 > budget:
            return emit("stopped: time budget used", 0.6, final=False, timed_out=True)
        t = time.time()
        with_chips = plan_one(shared, state, rules, fts[0], inputs, chips=True)
        timings["chip_search_seconds"] = round(time.time() - t, 1)
        plans[fts[0]] = with_chips
        verdicts[fts[0]] = rails(shared, state, rules, fts[0], with_chips)
    timings["total_seconds"] = round(time.time() - t0, 1)
    return emit("final", 1.0, final=True)


# ---- reporting ---------------------------------------------------------------------------------
def _player(shared: Shared, pid: int, gi: int = 0) -> dict[str, Any]:
    pl = shared.players.set_index("player_id")
    rows = {p: i for i, p in enumerate(shared.proj.player_ids)}
    out: dict[str, Any] = {"id": int(pid), "name": shared.names.get(int(pid), str(pid))}
    if pid in pl.index:
        r = pl.loc[pid]
        out.update(
            {
                "fpl_id": int(r["fpl_id"]),
                "team": r["team"],
                "position": r["position"],
                "price": int(r["price"]),
                "status": r["status"],
                "chance": None
                if r["chance"] is None or r["chance"] != r["chance"]
                else float(r["chance"]),
                "news": r["news"] or "",
            }
        )
    if pid in rows:
        i = rows[pid]
        out["xp"] = round(float(shared.proj.points[i, gi, :].mean()), 2)
        mt = shared.proj.minutes_table
        if mt is not None:
            m = mt[(mt["player_id"] == pid) & (mt["gw"] == shared.proj.gws[gi])]
            if len(m):
                out["p_start"] = round(float(m["p_start"].iloc[0]), 3)
    else:
        out["xp"] = 0.0
    return out


def _decision(shared: Shared, d: Decision, verdict: dict) -> dict[str, Any]:
    P = lambda p: _player(shared, p)  # noqa: E731
    names = shared.names
    cmp = []
    for r in d.comparison:
        cmp.append(
            {
                **{
                    k: r[k]
                    for k in (
                        "transfers",
                        "hits",
                        "feasible",
                        "week_xp",
                        "horizon_xp",
                        "objective",
                        "win_share",
                        "gain",
                        "passes",
                        "reason",
                        "solve",
                        "gain_sd",
                        "gain_q10",
                        "gain_q90",
                        "worlds",
                        "week_xp_gross",
                    )
                    if k in r
                },
                "moves": [
                    {"out": names.get(o, str(o)), "in": names.get(i, str(i))}
                    for i, o in zip(r.get("in", []), r.get("out", []), strict=False)
                ],
                "captain": names.get(r.get("captain")) if r.get("captain") else None,
            }
        )
    roll = next((r for r in d.comparison if r["transfers"] == 0), None)
    chosen = next((r for r in d.comparison if r["transfers"] == len(d.transfers_in)), None)
    return {
        "gw": d.gw,
        "action": d.action,
        "chip": d.chip,
        "moves": [
            {"out": P(o), "in": P(i)} for i, o in zip(d.transfers_in, d.transfers_out, strict=False)
        ],
        "hits": d.hits,
        "hit_cost": 4 * d.hits,
        "captain": P(d.captain),
        "vice": P(d.vice),
        "xi": [P(p) for p in d.xi],
        "bench": [P(p) for p in d.bench],
        "xp": round(d.xp, 2),
        "xp_after_hits": round(d.xp - 4 * d.hits, 2),
        "xp_no_transfers": round(d.xp_no_transfers, 2),
        "objective": None if d.objective is None else round(d.objective, 2),
        "bank_after": d.bank_after,
        "ft_after": d.ft_after,
        "solve": d.solve,
        "captain_options": [
            {
                "player": P(o["player_id"]),
                "xp": round(o["xpts"], 2),
                "p_haul": round(o["p_haul"], 3),
            }
            for o in d.captain_options
        ],
        "chips": {
            "play_now": d.chip_report.get("play_now"),
            "held": d.chip_report.get("held", {}),
            "scenarios": d.chip_report.get("scenarios", []),
            "searched": bool(d.chip_report.get("scenarios")) or bool(d.chip_report.get("held")),
        },
        "comparison": cmp,
        "comparison_note": d.comparison_note,
        "baseline": None
        if roll is None
        else {
            "xi": [P(p) for p in roll.get("xi", [])],
            "bench": [P(p) for p in roll.get("bench", [])],
            "captain": P(roll["captain"]) if roll.get("captain") else None,
            "week_xp": roll["week_xp"],
            "horizon_xp": roll["horizon_xp"],
        },
        "chosen_row": chosen,
        "horizon": [
            {
                "gw": w.gw,
                "moves": [
                    {"out": names.get(o, str(o)), "in": names.get(i, str(i))}
                    for i, o in zip(w.transfers_in, w.transfers_out, strict=False)
                ],
                "hits": w.hits,
                "chip": w.chip,
                "captain": names.get(w.captain),
                "xp": round(w.xp, 2),
                "ft_before": w.ft_before,
            }
            for w in d.horizon
        ],
        "warnings": d.warnings,
        "checks": d.checks,
        "field": d.field_view,
        "rails": verdict,
    }


def _fact(f, now: datetime, kind: str) -> dict[str, Any]:
    """A field's value with a visitor-facing status: verified (confirmed now), verified at the
    last deadline (public picks, may have changed since), estimated (reconstructed) or unknown."""
    if f.status == CONFIRMED:
        label = "verified"
    elif f.status == INFERRED and f.source == "public data":
        label = "as of last deadline"
    elif f.status == INFERRED:
        label = "estimated"
    elif f.status == CONFLICTING:
        label = "conflicting"
    else:
        label = "unknown"
    return {
        "value": f.value,
        "status": label,
        "source": f.source,
        "observed_at": f.observed_at.isoformat() if f.observed_at else None,
        "note": f.note,
        "kind": kind,
    }


def team_facts(state: PublicState) -> dict[str, Any]:
    t, now = state.team, state.now
    return {
        "gameweek": t.gameweek,
        "squad": _fact(t.squad, now, "squad"),
        "bank": _fact(t.bank, now, "money"),
        "free_transfers": _fact(t.free_transfers, now, "count"),
        "transfers_made": _fact(t.transfers_made, now, "count"),
        "chips": {k: _fact(v, now, "chip") for k, v in sorted(t.chips.items())},
        "ft_scenarios": t.ft_scenarios(),
        "selling_prices": "estimated from FPL's half-profit rule and purchase prices in the "
        "public transfer history",
        "not_public": [
            f"transfers made for GW{t.gameweek} (public only after its deadline)",
            "the exact free-transfer count (FPL never publishes it)",
            "current bank and selling prices after any unseen transfers",
        ],
    }


def conditions(
    shared: Shared, d: Decision, plans: dict[int, Decision], fts: list[int], team: CurrentTeam
) -> list[str]:
    """Plain statements of what would change the recommendation."""
    n = shared.names
    out = []
    held = [r for r in d.comparison if r["transfers"] and r.get("feasible") and not r["passes"]]
    if not d.transfers_in and held:
        best = max(held, key=lambda r: r.get("gain") or -99)
        if best.get("win_share") is not None:
            out.append(
                f"The best {best['transfers']}-transfer option beat rolling in "
                f"{best['win_share']:.0%} of simulated outcomes ({best['gain']:+.1f} pts "
                "over the plan). It would be recommended at 55% or +1.0 pts."
            )
    if len({action_key(x) for x in plans.values()}) > 1:
        out.append(
            "The transfer advice depends on how many free transfers you have; check "
            "the count on FPL's Transfers page."
        )
    pl = shared.players.set_index("player_id")
    doubt = [
        p
        for p in d.xi
        if p in pl.index
        and (
            pl.loc[p, "status"] != "a"
            or (
                pl.loc[p, "chance"] == pl.loc[p, "chance"]
                and pl.loc[p, "chance"] is not None
                and float(pl.loc[p, "chance"]) < 100
            )
        )
    ]
    if doubt and d.bench:
        out.append(
            f"If {', '.join(n.get(p, str(p)) for p in doubt)} is ruled out, the first "
            f"bench player ({n.get(d.bench[0], d.bench[0])}) comes in automatically."
        )
    if len(d.captain_options) > 1:
        a, b = d.captain_options[0], d.captain_options[1]
        if abs(a["xpts"] - b["xpts"]) < 0.5:
            out.append(
                f"Captaincy is close: {n.get(a['player_id'])} {a['xpts']:.1f} vs "
                f"{n.get(b['player_id'])} {b['xpts']:.1f} expected points."
            )
    if team.squad.status != CONFIRMED:
        out.append(
            f"If you have already made transfers for GW{team.gameweek}, this plan is "
            "based on an older squad: FPL shows them publicly only after the deadline."
        )
    return out


def payload(
    shared: Shared,
    state: PublicState,
    rules: Rules,
    prefs: dict,
    plans: dict[int, Decision],
    verdicts: dict[int, dict],
    fts: list[int],
    inputs,
    final: bool,
    timings: dict,
    timed_out: bool,
    chips_possible: bool,
) -> dict:
    p = prefs_of(prefs)
    primary = plans[fts[0]]
    scen = []
    for ft in fts:
        if ft not in plans:
            scen.append({"free_transfers": ft, "status": "pending"})
            continue
        x = plans[ft]
        scen.append(
            {
                "free_transfers": ft,
                "status": "planned",
                "action": x.action,
                "key": list(map(str, action_key(x))),
                "moves": [
                    {"out": shared.names.get(o), "in": shared.names.get(i)}
                    for i, o in zip(x.transfers_in, x.transfers_out, strict=False)
                ],
                "hits": x.hits,
                "captain": shared.names.get(x.captain),
                "xp": round(x.xp - 4 * x.hits, 2),
                "chips_searched": bool(x.chip_report.get("scenarios")),
            }
        )
    planned = [s for s in scen if s["status"] == "planned"]
    same = len({tuple(s["key"]) for s in planned}) == 1
    complete = len(planned) == len(fts)
    lineup_ok = not verdicts[fts[0]]["lineup_blocked"]
    return {
        "version": PLAN_VERSION,
        "stage": "final" if final else ("timed_out" if timed_out else "preliminary"),
        "final": final,
        "timed_out": timed_out,
        "created_at": time.time(),
        "gameweek": state.gw,
        "entry_id": state.entry_id,
        "strategy": {
            "key": p["strategy"],
            **STRATEGIES[p["strategy"]],
            "target_rank": p["target_rank"] if p["strategy"] == "rank" else None,
            "notes": inputs[4],
        },
        "forecast": {
            "key": shared.key,
            "cutoff": shared.manifest["cutoff"],
            "created_at": shared.manifest["created_at"],
            "model": shared.manifest["model"],
            "samples": shared.manifest["samples"],
            "revision": shared.manifest["revision"],
        },
        "team": team_facts(state),
        "sources": state.sources,
        "assumptions": [
            f"Squad: your GW{state.gw - 1} deadline squad plus any transfers FPL already "
            "lists publicly.",
            f"Free transfers: planned for {', '.join(str(x) for x in fts)} (estimate "
            f"{state.team.free_transfers.value}; FPL does not publish the count).",
            "Selling prices: estimated with FPL's half-profit rule.",
        ],
        "primary_ft": fts[0],
        "scenarios": scen,
        "conditional": complete and not same,
        "same_across_scenarios": complete and same,
        "chips_possible": chips_possible,
        "recommendation": _decision(shared, primary, verdicts[fts[0]]),
        "lineup_shown": lineup_ok,
        "conditions": conditions(shared, primary, plans, fts, state.team),
        "timings": timings,
        "min_chance": MIN_CHANCE,
        "unknown_states": [UNKNOWN, CONFLICTING],
    }


__all__ = [
    "PublicState",
    "load_state",
    "fingerprint",
    "plan_key",
    "prefs_of",
    "run",
    "team_facts",
    "STRATEGIES",
    "TARGETS",
    "PlanUnavailable",
]
