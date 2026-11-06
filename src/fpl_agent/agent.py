"""The weekly run, end to end: gate -> data -> squad -> (odds, scout) -> project -> decide ->
rails -> execute (dry by default) -> record -> report.

Every optional piece (odds key, scout key, entry id) degrades to a skipped step with a note in the
report; the run only fails outright when it cannot build a projection or read the squad.
"""

from __future__ import annotations

import json
import logging
import os
import uuid
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from pathlib import Path

from fpl_agent import learning, lessons, notify, pit, review
from fpl_agent.config import Settings
from fpl_agent.data import oddsapi, ownership
from fpl_agent.data.calendar import hours_to_deadline, in_action_window, next_event
from fpl_agent.data.entry import parse_squad
from fpl_agent.data.fpl_client import FPLClient
from fpl_agent.db.database import FootballDB
from fpl_agent.execution import safety
from fpl_agent.execution.execute import Outcome, execute
from fpl_agent.execution.live_state import Signed, read_live
from fpl_agent.model import calibration, overlays, project
from fpl_agent.model.simulate import SimConfig
from fpl_agent.rules import Rules
from fpl_agent.scout import Scout
from fpl_agent.solver import rank_target, timing
from fpl_agent.solver.decide import ROBUST_MIN_WIN, Decision, decide, rank_risk
from fpl_agent.solver.inputs import to_canonical
from fpl_agent.solver.planner import PlannerConfig

log = logging.getLogger(__name__)


@dataclass
class RunOptions:
    season: int = 2026
    horizon: int = 8  # long enough to plan the Wildcard squad for a later Bench Boost
    entry_id: int | None = None
    force: bool = False  # skip the deadline-window gate
    use_chips: bool = True
    samples: int = 1000
    out_dir: Path | None = None
    notify: bool = False
    team_dir: Path | None = None  # stored screen reads and corrections (git-ignored)


@dataclass
class RunResult:
    status: str  # "ok" | "skipped" | "error"
    gw: int | None = None
    decision: Decision | None = None
    outcome: Outcome | None = None
    notes: list[str] = field(default_factory=list)
    report: str = ""
    team: object | None = None  # the merged CurrentTeam the plan was built from (dry mode)
    sections: list[str] = field(default_factory=list)  # comparison, scenarios, questions
    scenarios: list[dict] = field(default_factory=list)  # the advice per free-transfer balance

    @property
    def exit_code(self) -> int:
        failed_moves = bool(self.outcome and self.outcome.errors)
        return 2 if self.status == "error" or failed_moves else 0


def _names(db: FootballDB) -> dict[int, str]:
    return dict(db.con.execute("SELECT player_id, name FROM players").fetchall())


def format_report(res: RunResult, mode: str) -> str:
    head = f"fpl-agent weekly plan ({mode.upper()} mode)"
    if res.status != "ok" or not res.decision:
        return "\n".join([head, f"status: {res.status}", *res.notes])
    d, o = res.decision, res.outcome
    lines = [head]
    if res.team is not None:
        from fpl_agent.team.report import team_lines

        lines += team_lines(res.team)
    lines.append(d.summary())
    if d.xp_no_transfers:
        lines.append(f"vs. no moves: {d.xp - d.xp_no_transfers:+.1f} expected points this week")
    if d.objective is not None:
        lines.append(
            f"planner objective of the followed plan: {d.objective:.1f} (not points: "
            "discounted, bench-weighted, with banked-transfer and bank value)"
        )
    lines += res.sections
    if o:
        lines += [f"{'DID' if o.mode == 'live' else 'dry-run'}: {x}" for x in o.done]
        lines += [f"blocked by rails: {x}" for x in o.blocked]
        lines += [f"error: {x}" for x in o.errors]
    lines += [f"note: {n}" for n in res.notes]
    return "\n".join(lines)


def run_weekly(
    db: FootballDB,
    settings: Settings,
    opts: RunOptions,
    fpl: FPLClient | None = None,
    scout: Scout | None = None,
    odds: oddsapi.OddsClient | None = None,
    now: datetime | None = None,
    exchange=None,
    http=None,
) -> RunResult:
    res = RunResult("ok")
    if not settings.enabled:
        res.status = "skipped"
        res.notes.append("kill switch is on (AGENT_ENABLED=false)")
        res.report = format_report(res, settings.mode)
        return res
    fpl = fpl or FPLClient()
    try:
        bs = fpl.bootstrap()
        raw = fpl.bootstrap_raw()
        ev = next_event(bs)
    except Exception as exc:  # noqa: BLE001
        res.status, res.notes = "error", [f"could not read FPL bootstrap: {type(exc).__name__}"]
        res.report = format_report(res, settings.mode)
        return res
    if ev is None:
        res.status = "skipped"
        res.notes.append("no upcoming gameweek")
        res.report = format_report(res, settings.mode)
        return res
    res.gw = ev.id
    if not opts.force and not in_action_window(ev, now or datetime.now(UTC)):
        res.status = "skipped"
        res.notes.append(
            f"deadline is {hours_to_deadline(ev, now):.0f}h away, outside the action window"
        )
        res.report = format_report(res, settings.mode)
        return res

    rules = Rules.nearest(opts.season)
    entry = opts.entry_id or settings.entry_id
    if entry is None:
        res.status, res.notes = "error", ["FPL_ENTRY_ID is not set: cannot read your squad"]
        res.report = format_report(res, settings.mode)
        return res
    try:
        picks = fpl.entry_picks_raw(entry, ev.id - 1) if ev.id > 1 else {"picks": []}
        transfers = fpl.entry_transfers_raw(entry)
        history = fpl.entry_history_raw(entry)
        state = parse_squad(picks, transfers, history, raw, ev.id, rules)
    except Exception as exc:  # noqa: BLE001
        res.status, res.notes = "error", [f"could not read your squad: {type(exc).__name__}"]
        res.report = format_report(res, settings.mode)
        return res

    run_at = now or datetime.now(UTC)
    team = None
    if not settings.is_live:  # (live mode reads the team FPL holds now, below)
        try:
            team = _current_team(fpl, opts, entry, ev.id, rules, raw, picks, transfers, history,
                                 run_at)  # fmt: skip
        except Exception as exc:  # noqa: BLE001  (keep the public last-deadline state)
            log.warning("current-team merge failed: %s", exc)
            res.notes.append(f"current team not merged ({type(exc).__name__}): planned from the "
                             "public last-deadline state")  # fmt: skip
        if team is not None and not team.usable:
            from fpl_agent.team.report import team_lines

            res.status = "error"
            res.notes = [*team_lines(team), *(f"needed: {q}" for q in team.questions(True))]
            res.report = format_report(res, settings.mode)
            return res
        if team is not None:
            from fpl_agent.team.merge import to_state

            state = to_state(team, raw, rules, team.ft_scenarios()[0])
            res.team = team

    tokens, live_changed, live_ok = None, False, True
    if settings.is_live:  # plan from the team FPL has now, not the last deadline's
        before = state
        state, tokens, live_changed, note = read_live(state, raw, rules, entry, exchange, http)
        live_ok = state is not before
        res.notes.append(note)

    pool = project.current_pool(db, opts.season, ev.id)
    if pool.empty:
        res.status, res.notes = (
            "error",
            [f"database has no player snapshot for GW{ev.id}: run db-update"],
        )
        res.report = format_report(res, settings.mode)
        return res
    try:
        state = to_canonical(state, pool)
    except ValueError as exc:
        res.status, res.notes = "error", [str(exc)]
        res.report = format_report(res, settings.mode)
        return res

    hand: list[int] = []
    if settings.is_live and live_ok:
        hand = manual_changes(db, opts.season, ev.id, state)
        if hand:  # you changed the team after the bot's last moves: keep it, set the lineup only
            state = replace(state, already_transferred=max(state.already_transferred, len(hand)),
                            wildcard_active=False, remaining_free_transfers=None)  # fmt: skip
            live_changed = True
            res.notes.append(
                f"you changed {len(hand)} player(s) by hand after the bot's last moves: kept, "
                "only the lineup and captain are set"
            )

    odds = odds or oddsapi.OddsClient()
    if odds.enabled:
        try:
            res.notes.append(f"odds: {oddsapi.load_events(db, odds.upcoming())} matches priced")
        except Exception as exc:  # noqa: BLE001
            res.notes.append(f"odds skipped ({type(exc).__name__})")
    else:
        res.notes.append("odds skipped: no ODDS_API_KEY")
    scout = scout or Scout()
    if scout.enabled:
        pool, adj = scout.run(pool)
        res.notes.append(f"scout adjusted {len(adj)} players")
    else:
        res.notes.append("scout skipped: no ANTHROPIC_API_KEY")

    pressure = timing.price_pressure(raw)
    own, eo, own_note = _ownership(db, fpl, opts.season, ev.id, pool)
    res.notes.append(own_note)

    gws = list(range(ev.id, min(ev.id + opts.horizon, 39)))
    cutoff = pit.utc(run_at)  # the information cutoff of this run (naive UTC), also its stamp
    try:
        fc = forecast(db, opts.season, gws, rules, pool, ev.id, cutoff, opts.samples)
        proj = fc.proj
        res.notes += fc.notes
        names = _names(db)
        res.notes += explain_overlays(fc.overlays, proj, names, shadow=False)
        res.notes += run_experiments(db, fc, rules, pool, names)
        # what the planner receives: the raw forecast, then the position calibration, then the
        # memory's template correction; recorded as the issued forecast (raw stays for learning)
        if fc.calibration:
            calibration.apply(proj, fc.calibration)
            res.notes.append(
                "learning: position calibration "
                + ", ".join(f"{k} x{v:.3f}" for k, v in sorted(fc.calibration.items()))
                + (f" (learning state v{fc.learning_version})" if fc.learning_version else "")
            )
        mem = lessons.memory(db, opts.season, ev.id)
        core = mem.apply_template(proj, own if eo else {})  # only with the top-10k ownership
        res.notes += mem.notes()
        record_issued(db, fc, {"calibration": fc.calibration,
                               "template": {"factor": mem.template if core else 1.0,
                                            "players": core}})  # fmt: skip
        target = _chase(fpl, history, ev.id)
        if target:
            res.notes.append(target.note())
        cfg = mem.planner(PlannerConfig(risk_lambda=rank_risk(ev.id) if eo else 0.0))

        def plan_for(st, chips: bool = True):  # one forecast, configuration and thresholds
            return decide(st, proj, rules, names=names, cfg=cfg,
                          use_chips=chips and opts.use_chips and not live_changed,
                          ownership=own, eo=eo, chasing=target,
                          min_win=mem.min_win(ROBUST_MIN_WIN))  # fmt: skip

        d = plan_for(state)
        if team is not None:
            res.sections, res.scenarios = _team_sections(team, d, plan_for, raw, rules, pool,
                                                         names)  # fmt: skip
        else:  # live mode: the team FPL holds now; still show what was compared
            from fpl_agent.team.report import chip_solver_lines, comparison_lines

            res.sections = comparison_lines(d, names) + chip_solver_lines(d)
    except Exception as exc:  # noqa: BLE001
        log.exception("projection/decision failed")
        res.status, res.notes = (
            "error",
            [*res.notes, f"model run failed: {type(exc).__name__}: {exc}"],
        )
        res.report = format_report(res, settings.mode)
        return res
    res.decision = d
    first_time = not db.con.execute(
        "SELECT count(*) FROM decisions WHERE season = ? AND gw = ?", [opts.season, d.gw]
    ).fetchone()[0]  # a later run in the same window re-plans but does not re-notify
    review.save_decision(db, opts.season, d, settings.mode)

    mean = dict(zip(proj.meta["player_id"], proj.mean()[:, 0], strict=True))
    recent = {r["event"]: r.get("event_transfers_cost", 0) // 4 for r in history.get("current", [])}
    ctx = safety.Context(
        settings, state, pool[["player_id", "team_id", "position", "status", "chance"]], mean,
        bootstrap_next_gw=ev.id, recent_hits=recent,
        projected_share=len(proj.meta) / max(len(pool), 1),
    )  # fmt: skip
    fpl_id = dict(zip(pool["player_id"].astype(int), pool["fpl_id"].astype(int), strict=True))
    price = dict(zip(pool["player_id"].astype(int), pool["price"].astype(int), strict=True))
    hold = False
    if not opts.force:  # a scheduled run waits for team news unless prices are about to move
        moving = {pid: p for pid, f in fpl_id.items() if (p := pressure.get(f))}
        at = now or datetime.now(UTC)
        hold, why = timing.hold_moves(
            d.transfers_in, d.transfers_out, moving, hours_to_deadline(ev, at),
            timing.update_before(ev.deadline_time, at), d.name,
        )  # fmt: skip
        if why:
            res.notes.append(why)
    if not live_ok:  # never send moves built on the last deadline's squad
        res.outcome = Outcome("live", errors=["your current FPL team could not be read: "
                                              "nothing was sent"])  # fmt: skip
    elif hold:
        res.outcome = Outcome("live" if settings.is_live else "dry")
    else:
        res.outcome = execute(
            d,
            ctx,
            rules,
            fpl_id,
            price,
            entry,
            exchange=(lambda: Signed(tokens)) if tokens else exchange,
            http=http,
        )
    o = res.outcome
    # the squad FPL now holds is the bot's (after hand changes it stays yours all gameweek)
    if o and o.mode == "live" and o.ok and not o.blocked and not hand:
        review.save_decision(db, opts.season, d, DONE)
    res.report = format_report(res, settings.mode)
    if opts.out_dir:
        write_outputs(opts.out_dir, res, settings.mode)
    o = res.outcome
    moved = bool(o and o.mode == "live" and o.done)
    failed = bool(o and o.errors and opts.force)  # a hand-started run reports its failure
    if opts.notify and (first_time or moved or failed):
        notify.send(res.report)
    return res


DONE = "live-done"  # decisions row mode once FPL accepted every move of a live run


def _current_team(fpl, opts: RunOptions, entry: int, gw: int, rules: Rules, raw: dict, picks,
                  transfers, history, now: datetime):  # fmt: skip
    """The team merged from public data and the manager's stored screen reads/corrections."""
    from fpl_agent.team import current_team, public_view

    pv = public_view(entry, gw, picks, transfers, history, raw, rules, now)
    return current_team(entry=entry, season=opts.season, gameweek=gw, now=now, rules=rules,
                        bootstrap=raw, public=pv, root=opts.team_dir,
                        fixtures=lambda: fpl.fixtures_raw())  # fmt: skip


def _team_sections(team, d: Decision, plan_for, raw: dict, rules: Rules, pool,
                   names: dict[int, str]) -> tuple[list[str], list[dict]]:  # fmt: skip
    """The transfer-count comparison, the advice under every plausible free-transfer balance
    (each planned with the same forecast and thresholds) and the specific questions left."""
    from fpl_agent.team.merge import to_state
    from fpl_agent.team.report import (
        action_key,
        chip_solver_lines,
        comparison_lines,
        scenario_lines,
    )

    out = comparison_lines(d, names) + chip_solver_lines(d)
    fts = team.ft_scenarios()
    alts = []
    if d.comparison:  # the free-transfer count can change the advice only when moves are open
        # the chip search already held every chip; it is not repeated per balance (runtime)
        for ft in fts[1:]:
            st = to_canonical(to_state(team, raw, rules, ft), pool)
            alts.append((ft, plan_for(st, chips=False)))
    elif len(fts) > 1:
        out.append(f"free transfers not confirmed (planned with {fts[0]}); transfer counts "
                   f"were not compared: {d.comparison_note}")  # fmt: skip
    lines, conditional = scenario_lines(fts[0], d, alts, team, names)
    if alts:
        lines.insert(1, "  (each balance re-planned with the same forecast and thresholds; "
                        "chips stay held as in the main plan)")  # fmt: skip
    out += lines
    if team.bank.status in ("unknown", "conflicting") and d.comparison:
        bank = int(team.bank.value or 0) / 10
        out.append(f"bank not confirmed: transfer options assume £{bank:.1f}m; more money "
                   "could open other moves")  # fmt: skip
        conditional = True
    out += [f"needed: {q}" for q in team.questions(conditional)]
    rows = [{"free_transfers": ft, "action": list(action_key(x)), "captain": x.captain,
             "xp": round(x.xp, 2), "objective": x.objective, "comparison": x.comparison}
            for ft, x in [(fts[0], d), *alts]]  # fmt: skip
    return out, rows


def explain_overlays(ovs, proj, names: dict[int, str], shadow: bool) -> list[str]:
    """Report lines for every next-gameweek change an overlay made."""
    tag = "shadow, not used by the plan" if shadow else "used by the plan"
    out = []
    for ov in ovs:
        if ov.name == "workload" and ov.changed:
            out.append(f"workload ({tag}): P(start) nudged for {ov.changed} players "
                       f"(bounded +-{ov.bound:.2f}); {ov.counts}")  # fmt: skip
    return out


@dataclass
class Forecast:
    """The raw primary forecast of a run, as saved, with the configuration that made it."""

    proj: project.Projection
    run_id: str
    created_at: datetime
    kind: str
    modes: dict[str, str]
    features_on: list[str]
    calibration: dict[str, float]
    learning_version: int | None
    samples: int
    seed: int
    overlays: list = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    cache: dict = field(default_factory=dict, repr=False)


def active_config(db: FootballDB, season: int, gw: int) -> tuple[dict, dict, int | None]:
    """(feature modes, calibration factors, learning-state version) the plan runs with: the
    current learning state when there is one (environment flags still win for the modes),
    otherwise the built-in defaults (shadow features, calibration from final reviews)."""
    lstate = learning.current_state(db)
    cfg = lstate["config"] if lstate else {}
    modes = overlays.modes(cfg.get("features"))
    fac = dict(cfg.get("calibration") or {}) if lstate else calibration.factors(db, season, gw)
    return modes, fac, (int(lstate["version"]) if lstate else None)


def forecast(db: FootballDB, season: int, gws: list[int], rules: Rules, pool, pool_gw: int,
             cutoff: datetime, samples: int, kind: str = "live") -> Forecast:  # fmt: skip
    """Project with the features that are `on` and save the raw run, stamped at the cutoff,
    with its full configuration (modes, features applied, calibration, samples, seed)."""
    modes, fac, version = active_config(db, season, gws[0])
    on = sorted(f for f in overlays.FEATURES if modes[f] == "on")
    cache: dict = {}
    ovs = overlays.build(on, db, cache)
    cfg = SimConfig(n=samples)
    proj = project.build_projection(db, season, gws, rules=rules, pool=pool, cfg=cfg,
                                    pool_gw=pool_gw, cutoff=cutoff, overlays=ovs)  # fmt: skip
    config = {"features": modes, "calibration": fac, "learning_version": version}
    project.save_projection(db, proj, created_at=cutoff, kind=kind, extra_flags={
        "role": "primary", "features_on": on, "modes": modes, "config": config,
        "samples": cfg.n, "seed": cfg.seed})  # fmt: skip
    notes = ["features: " + ", ".join(f"{k}={v}" for k, v in modes.items())]
    return Forecast(proj, proj.run_id, cutoff, kind, modes, on, fac, version, cfg.n, cfg.seed,
                    ovs, notes, cache)  # fmt: skip


def experiment_arms(modes: dict[str, str]) -> list[tuple[str, list[str]]]:
    """(role, features applied) of each experiment arm: the issued configuration again (the
    null intervention), plus that configuration with each shadow feature added and with each
    `on` feature removed, so every feature that is not `off` has a matched comparison that
    differs from its partner in that feature only."""
    on = sorted(f for f in overlays.FEATURES if modes[f] == "on")
    arms = [("control", on)]
    for f in overlays.FEATURES:
        if modes[f] == "shadow":
            arms.append((f"with:{f}", sorted({*on, f})))
        elif modes[f] == "on":
            arms.append((f"without:{f}", sorted(set(on) - {f})))
    return arms


NULL_TOLERANCE = 1e-9


def run_experiments(db: FootballDB, fc: Forecast, rules: Rules, pool,
                    names: dict[int, str]) -> list[str]:  # fmt: skip
    """The next gameweek re-simulated once per experiment arm with the primary's fitted models,
    sample count and seeds, stored as `experiment` runs beside the primary. The control arm must
    reproduce the primary exactly (null-intervention check); if it does not, every arm of the
    run is marked invalid. Any failure is reported and ignored: experiments never block a plan."""
    gw = fc.proj.gws[0]
    inp = fc.proj.inputs
    out: list[str] = []
    try:
        primary = dict(zip(fc.proj.player_ids, fc.proj.mean()[:, 0].astype(float), strict=True))
        built = []
        for role, feats in experiment_arms(fc.modes):
            ovs = overlays.build(feats, db, fc.cache)
            arm = project.build_projection(
                db, fc.proj.season, [gw], rules=rules, pool=pool, pool_gw=gw, cutoff=fc.proj.cutoff,
                cfg=SimConfig(n=fc.samples, seed=fc.seed), overlays=ovs,
                minutes_model=inp.get("minutes_model"), ratings=inp.get("ratings"),
                frame=inp.get("frame"), pm=inp.get("pm"),
            )  # fmt: skip
            arm.variant = "experiment"
            built.append((role, feats, arm, ovs))
        control = next(a for r, _, a, _ in built if r == "control")
        diff = max((abs(float(v) - primary[p]) for p, v in
                    zip(control.player_ids, control.mean()[:, 0], strict=True) if p in primary),
                   default=float("inf"))  # fmt: skip
        null = {"max_abs_diff": diff, "passed": bool(diff <= NULL_TOLERANCE)}
        for role, feats, arm, ovs in built:
            project.save_projection(db, arm, created_at=fc.created_at, kind=fc.kind,
                                    parent_run_id=fc.run_id, extra_flags={
                                        "role": role, "features_on": feats,
                                        "samples": fc.samples, "seed": fc.seed,
                                        "null_check": null})  # fmt: skip
            if role != "control":
                out += explain_overlays([o for o in ovs if o.name not in fc.features_on], arm,
                                        names, shadow=True)  # fmt: skip
        out.insert(
            0,
            f"experiments stored: {', '.join(r for r, *_ in built)}"
            + (
                ""
                if null["passed"]
                else f" (INVALID: control differs from the plan by {diff:.3g} points)"
            ),
        )
    except Exception as exc:  # noqa: BLE001  (experiments are optional)
        log.warning("experiment arms failed: %s", exc)
        return [f"experiments skipped ({type(exc).__name__})"]
    return out


def record_issued(db: FootballDB, fc: Forecast, transformations: dict) -> str:
    """Save the forecast exactly as the planner receives it (after calibration and the template
    correction) as the run's `issued` variant, with every transformation that produced it."""
    issued = replace(fc.proj, run_id=uuid.uuid4().hex[:12], variant="issued")
    project.save_projection(db, issued, created_at=fc.created_at, kind=fc.kind,
                            parent_run_id=fc.run_id, extra_flags={
                                "role": "issued", "features_on": fc.features_on,
                                "transformations": transformations})  # fmt: skip
    return issued.run_id


def manual_changes(db, season: int, gw: int, state) -> list[int]:
    """Players in the current squad that the bot's last completed live run did not pick: you
    changed them by hand since. Empty when the bot has not completed a run this gameweek."""
    row = db.con.execute(
        "SELECT payload FROM decisions WHERE season = ? AND gw = ? AND mode = ? "
        "ORDER BY created_at DESC LIMIT 1",
        [season, gw, DONE],
    ).fetchone()
    if not row:
        return []
    p = json.loads(row[0])
    planned = set(p.get("xi", [])) | set(p.get("bench", []))
    if len(planned) != 15:
        return []
    return sorted(q.player_id for q in state.squad if q.player_id not in planned)


def _chase(fpl, history: dict, gw: int) -> rank_target.Chase | None:
    """Where you stand against the rank target; None (normal play) when it cannot be read."""
    try:
        return rank_target.chase(fpl, history, gw)
    except Exception as exc:  # noqa: BLE001  (the standings are optional: plan without them)
        log.warning("rank target unreadable: %s", type(exc).__name__)
        return None


def _ownership(db, fpl, season: int, gw: int, pool):
    """(ownership %, effective ownership as a fraction, note), both keyed by canonical id:
    from a sample of the top 10k when it can be read, else FPL's overall ownership (and no
    effective ownership). Empty when AGENT_ALLOW_TEMPLATE_SELL=true lets the plan ignore it."""
    if os.environ.get("AGENT_ALLOW_TEMPLATE_SELL", "").strip().lower() == "true":
        return {}, {}, "ownership ignored: AGENT_ALLOW_TEMPLATE_SELL=true"
    pid = dict(zip(pool["fpl_id"].astype(int), pool["player_id"].astype(int), strict=True))
    field = ownership.field_ownership(db, fpl, season, gw)
    if not field.empty:
        n = int(field["sample"].iloc[0])
        rows = [(pid[int(r.fpl_id)], float(r.owned), float(r.eo))
                for r in field.itertuples() if int(r.fpl_id) in pid]  # fmt: skip
        return ({p: o for p, o, _ in rows}, {p: e / 100 for p, _, e in rows},
                f"ownership: top-10k sample of {n} managers")  # fmt: skip
    if "owned" not in pool:
        return {}, {}, "ownership unavailable"
    o = pool[["player_id", "owned"]].dropna()
    return (dict(zip(o["player_id"].astype(int), o["owned"].astype(float), strict=True)), {},
            "ownership: FPL overall (top-10k sample unavailable)")  # fmt: skip


def write_outputs(out: Path, res: RunResult, mode: str) -> None:
    out.mkdir(parents=True, exist_ok=True)
    (out / f"gw{res.gw}.md").write_text(res.report + "\n")
    if res.decision:
        (out / f"gw{res.gw}.json").write_text(json.dumps({
            "mode": mode, "decision": review.decision_payload(res.decision),
            "outcome": res.outcome.__dict__ if res.outcome else None,
            "chips": res.decision.chip_report,
            "objective": res.decision.objective, "solve": res.decision.solve,
            "comparison": res.decision.comparison,
            "team": res.team.as_dict() if res.team is not None else None,
            "ft_scenarios": res.scenarios}, indent=2, default=str))  # fmt: skip


__all__ = ["RunOptions", "RunResult", "run_weekly", "format_report", "write_outputs"]
