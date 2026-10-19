"""The weekly run, end to end: gate -> data -> squad -> (odds, scout) -> project -> decide ->
rails -> execute (dry by default) -> record -> report.

Every optional piece (odds key, scout key, entry id) degrades to a skipped step with a note in the
report; the run only fails outright when it cannot build a projection or read the squad.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from pathlib import Path

from fpl_agent import notify, review
from fpl_agent.config import Settings
from fpl_agent.data import oddsapi, ownership
from fpl_agent.data.calendar import hours_to_deadline, in_action_window, next_event
from fpl_agent.data.entry import parse_squad
from fpl_agent.data.fpl_client import FPLClient
from fpl_agent.db.database import FootballDB
from fpl_agent.execution import safety
from fpl_agent.execution.execute import Outcome, execute
from fpl_agent.execution.live_state import Signed, read_live
from fpl_agent.model import calibration, project
from fpl_agent.model.simulate import SimConfig
from fpl_agent.rules import Rules
from fpl_agent.scout import Scout
from fpl_agent.solver import rank_target, timing
from fpl_agent.solver.decide import Decision, decide, rank_risk
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


@dataclass
class RunResult:
    status: str  # "ok" | "skipped" | "error"
    gw: int | None = None
    decision: Decision | None = None
    outcome: Outcome | None = None
    notes: list[str] = field(default_factory=list)
    report: str = ""

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
    lines = [head, d.summary()]
    if d.xp_no_transfers:
        lines.append(f"vs. no moves: {d.xp - d.xp_no_transfers:+.1f} expected points this week")
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
                            wildcard_active=False)  # fmt: skip
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
    try:
        proj = project.build_projection(db, opts.season, gws, rules=rules, pool=pool,
                                        cfg=SimConfig(n=opts.samples), pool_gw=ev.id)  # fmt: skip
        fac = calibration.factors(db, opts.season, ev.id)
        if fac:
            calibration.apply(proj, fac)
            res.notes.append(
                "learning: position calibration "
                + ", ".join(f"{k} x{v:.3f}" for k, v in sorted(fac.items()))
            )
        project.save_projection(db, proj)
        target = _chase(fpl, history, ev.id)
        if target:
            res.notes.append(target.note())
        cfg = PlannerConfig(risk_lambda=rank_risk(ev.id) if eo else 0.0)
        d = decide(state, proj, rules, names=_names(db), cfg=cfg,
                   use_chips=opts.use_chips and not live_changed,
                   ownership=own, eo=eo, chasing=target)  # fmt: skip
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
        write_outputs(opts.out_dir, res, settings.mode, view=_view(db, d, pool, raw, proj))
    o = res.outcome
    moved = bool(o and o.mode == "live" and o.done)
    failed = bool(o and o.errors and opts.force)  # a hand-started run reports its failure
    if opts.notify and (first_time or moved or failed):
        notify.send(res.report)
    return res


DONE = "live-done"  # decisions row mode once FPL accepted every move of a live run


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


def _view(db, d: Decision, pool, raw, proj) -> dict | None:
    """The plan with names, clubs and expected points, as the dashboard shows it (None if it
    cannot be built: the report never depends on it)."""
    try:
        from fpl_agent import site

        return site.full_plan(d, site._Show(site.labels(pool, raw, _names(db)), proj))
    except Exception as exc:  # noqa: BLE001
        log.warning("dashboard view skipped: %s", type(exc).__name__)
        return None


def write_outputs(out: Path, res: RunResult, mode: str, view: dict | None = None) -> None:
    out.mkdir(parents=True, exist_ok=True)
    (out / f"gw{res.gw}.md").write_text(res.report + "\n")
    if res.decision:
        (out / f"gw{res.gw}.json").write_text(json.dumps({
            "mode": mode, "decision": review.decision_payload(res.decision),
            "outcome": res.outcome.__dict__ if res.outcome else None,
            "chips": res.decision.chip_report, "view": view, "notes": res.notes,
            "created_at": datetime.now(UTC).isoformat()}, indent=2, default=str))  # fmt: skip


__all__ = ["RunOptions", "RunResult", "run_weekly", "format_report", "write_outputs"]
