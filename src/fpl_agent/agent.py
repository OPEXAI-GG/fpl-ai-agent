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

from fpl_agent import lessons, notify, review, runs
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
from fpl_agent.solver.decide import ROBUST_MIN_WIN, Decision, decide, lineup_only, rank_risk
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
    run_id: str | None = None
    held: bool = False  # the moves wait for team news (the lineup is still set)
    snapshot_at: datetime | None = None
    projection_run: str | None = None
    decision_at: datetime | None = None
    delivered: bool | None = None  # None: nothing to send

    @property
    def live_blocked(self) -> bool:
        """A live run that the rails stopped as a whole (stale data, illegal squad, ...)."""
        o = self.outcome
        return bool(o and o.mode == "live" and any(b.startswith("[global]") for b in o.blocked))

    @property
    def exit_code(self) -> int:
        failed_moves = bool(self.outcome and self.outcome.errors)
        if self.status == "error" or failed_moves:
            return 2
        return 3 if self.live_blocked else 0

    @property
    def run_status(self) -> str:
        """For the run record: ok | skipped | held | blocked | failed | error."""
        if self.status != "ok":
            return self.status
        if self.outcome and self.outcome.errors:
            return "failed"
        if self.live_blocked:
            return "blocked"
        return "held" if self.held else "ok"

    def reasons(self) -> list[str]:
        o = self.outcome
        out = [] if self.status == "ok" else list(self.notes)
        if o:
            out += o.errors + [b for b in o.blocked if b.startswith("[global]")]
        return out


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
        if o.unverified:
            lines.append("not verifiable from FPL's reply: " + ", ".join(o.unverified))
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
    """One weekly run, recorded however it ends (see runs.py), its report written and sent.

    The report goes out once per gameweek (and again until it was delivered), whenever a live
    run changed the team, and once for each distinct failure or block of a live run (or any
    failure of a hand-started run)."""
    entry = opts.entry_id or settings.entry_id
    rec = runs.RunRecord(opts.season, entry, settings.mode if settings.enabled else "off")
    own_client = fpl is None
    fpl = fpl or FPLClient()
    try:
        res = _run(db, settings, opts, rec, fpl, scout, odds, now, exchange, http)
    except Exception as exc:  # noqa: BLE001 - a crash is still a recorded, reported run
        log.exception("weekly run crashed")
        res = RunResult("error", notes=[f"run crashed: {type(exc).__name__}: {exc}"])
    finally:
        if own_client and hasattr(fpl, "close"):
            fpl.close()
    res.run_id = rec.run_id
    res.report = format_report(res, settings.mode)
    if opts.out_dir:
        write_outputs(opts.out_dir, res, settings.mode)
    key, send = _delivery(db, opts, res, entry)
    if send:
        res.delivered = notify.send(res.report)
    try:
        runs.save(db, rec, gw=res.gw, status=res.run_status,
                  state=runs.state_of(res.decision, res.outcome), exit_code=res.exit_code,
                  reasons=res.reasons(), snapshot_at=res.snapshot_at,
                  projection_run=res.projection_run, decision_at=res.decision_at,
                  report_key=key if send else None, delivered=res.delivered)  # fmt: skip
    except Exception:  # noqa: BLE001 - the record must never hide the run's own outcome
        log.exception("run record not saved")
    return res


def _delivery(db: FootballDB, opts: RunOptions, res: RunResult, entry: int | None):
    """(report key, send?) for this run's report."""
    if not opts.notify or res.gw is None:
        return None, False
    o = res.outcome
    seen = lambda key: runs.delivered_before(db, opts.season, res.gw, entry, key)  # noqa: E731
    if o and o.mode == "live" and o.done:  # the team changed: always say so
        return "plan", True
    reasons = res.reasons()
    if reasons and (res.live_blocked or (o and o.mode == "live" and o.errors) or opts.force):
        key = "alert:" + runs.alert_key(reasons)
        return key, not seen(key)
    if res.decision is not None:  # the gameweek's plan, until it has been delivered once
        return "plan", not seen("plan")
    return None, False


def _run(db, settings, opts, rec, fpl, scout, odds, now, exchange, http) -> RunResult:
    res = RunResult("ok")
    if not settings.enabled:
        res.status = "skipped"
        res.notes.append("kill switch is on (AGENT_ENABLED is false or unreadable)")
        return res
    fpl = fpl or FPLClient()
    rec.start("bootstrap")
    try:
        raw = fpl.bootstrap_raw()
        bs = fpl.bootstrap(raw)
        ev = next_event(bs)
    except Exception as exc:  # noqa: BLE001
        res.status, res.notes = "error", [f"could not read FPL bootstrap: {type(exc).__name__}"]
        rec.end("bootstrap", "error")
        return res
    rec.end("bootstrap")
    if ev is None:
        res.status = "skipped"
        res.notes.append("no upcoming gameweek")
        return res
    res.gw = ev.id
    if not opts.force and not in_action_window(ev, now or datetime.now(UTC)):
        res.status = "skipped"
        res.notes.append(
            f"deadline is {hours_to_deadline(ev, now):.0f}h away, outside the action window"
        )
        return res

    rules = Rules.nearest(opts.season)
    entry = opts.entry_id or settings.entry_id
    if entry is None:
        res.status, res.notes = "error", ["FPL_ENTRY_ID is not set: cannot read your squad"]
        return res
    rec.start("squad")
    try:
        transfers = fpl.entry_transfers_raw(entry)
        history = fpl.entry_history_raw(entry)
        # after a Free Hit the squad reverts to the one from the week before it
        last = ev.id - 1
        if last > 1 and any(c["name"] == "freehit" and c["event"] == last
                            for c in history.get("chips", [])):  # fmt: skip
            last -= 1
        picks = fpl.entry_picks_raw(entry, last) if ev.id > 1 else {"picks": []}
        state = parse_squad(picks, transfers, history, raw, ev.id, rules)
    except Exception as exc:  # noqa: BLE001
        res.status, res.notes = "error", [f"could not read your squad: {type(exc).__name__}"]
        rec.end("squad", "error")
        return res
    rec.end("squad")

    tokens, live_changed, live_ok = None, False, True
    if settings.is_live:  # plan from the team FPL has now, not the last deadline's
        before = state
        state, tokens, live_changed, note = read_live(state, raw, rules, entry, exchange, http)
        live_ok = state is not before
        res.notes.append(note)

    pool = project.current_pool(db, opts.season, ev.id)
    res.snapshot_at = db.con.execute(
        "SELECT max(snapshot_at) FROM fpl_players_gw WHERE season = ? AND gw = ?",
        [opts.season, ev.id],
    ).fetchone()[0]  # when the FPL data the plan uses was fetched
    at = now or datetime.now(UTC)
    age = None if res.snapshot_at is None else (
        at.replace(tzinfo=None) - res.snapshot_at).total_seconds() / 3600  # fmt: skip
    if age is not None and age > settings.max_snapshot_hours:
        res.notes.append(f"FPL data is {age:.1f}h old (live runs need under "
                         f"{settings.max_snapshot_hours:g}h): run db-update")  # fmt: skip
    if pool.empty:
        res.status, res.notes = (
            "error",
            [f"database has no player snapshot for GW{ev.id}: run db-update"],
        )
        return res
    try:
        state = to_canonical(state, pool)
    except ValueError as exc:
        res.status, res.notes = "error", [str(exc)]
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
    ctx = {"season": opts.season, "gw": ev.id, "date": f"{at:%Y-%m-%d}",
           "first_kickoff": None if ev.deadline_time is None
           else f"{ev.deadline_time:%Y-%m-%d %H:%M} UTC (deadline)"}  # fmt: skip
    pool, adj = scout.run(pool, db=db, ctx=ctx, squad={p.player_id for p in state.squad})
    report = getattr(scout, "last_report", None)
    res.notes.append(report.note() if report else f"scout adjusted {len(adj)} players")
    from fpl_agent.news import evidence_notes

    squad_ids = {p.player_id for p in state.squad}
    squad_names = db.con.execute(
        "SELECT name FROM players WHERE player_id IN (SELECT unnest(?))", [list(squad_ids)]
    ).fetchall()
    res.notes.extend(evidence_notes(db, [r[0] for r in squad_names], at))

    pressure = timing.price_pressure(raw)
    own, eo, own_note = _ownership(db, fpl, opts.season, ev.id, pool)
    res.notes.append(own_note)

    gws = list(range(ev.id, min(ev.id + opts.horizon, 39)))
    stage = "model"
    rec.start(stage)
    try:
        proj = project.build_projection(db, opts.season, gws, rules=rules, pool=pool,
                                        cfg=SimConfig(n=opts.samples), pool_gw=ev.id,
                                        cutoff=at.replace(tzinfo=None))  # fmt: skip
        # the raw model output is what is stored and reviewed, so the calibration learned from
        # the reviews never feeds back into its own inputs; the scaled values drive the decision
        project.save_projection(db, proj)
        res.projection_run = proj.run_id
        if "rate_source" in proj.meta:
            prior = int(proj.meta["rate_source"].eq("prior").sum())
            res.notes.append(
                f"player-rate coverage: {prior}/{len(proj.meta)} players use position priors "
                "(no usable detailed appearance history); treat these forecasts cautiously"
                if prior
                else "player-rate coverage: all projected players have detailed history"
            )
        fac = calibration.factors(db, opts.season, ev.id)
        if fac:
            calibration.apply(proj, fac)
            res.notes.append(
                "learning: position calibration (decision only; stored projections are raw) "
                + ", ".join(f"{k} x{v:.3f}" for k, v in sorted(fac.items()))
            )
        # the memory: lessons from the gameweeks already reviewed, read before deciding. The
        # template correction comes after saving, so the reviews keep learning it from the
        # uncorrected projections.
        mem = lessons.memory(db, opts.season, ev.id)
        mem.apply_template(proj, own if eo else {})  # only with the top-10k sample's ownership
        res.notes += mem.notes()
        target = _chase(fpl, history, ev.id)
        if target:
            res.notes.append(target.note())
        cfg = mem.planner(PlannerConfig(risk_lambda=rank_risk(ev.id) if eo else 0.0))
        rec.end(stage)
        stage = "decision"
        rec.start(stage)
        d = decide(state, proj, rules, names=_names(db), cfg=cfg,
                   use_chips=opts.use_chips and not live_changed,
                   ownership=own, eo=eo, chasing=target,
                   min_win=mem.min_win(ROBUST_MIN_WIN))  # fmt: skip
    except Exception as exc:  # noqa: BLE001
        log.exception("projection/decision failed")
        res.status, res.notes = (
            "error",
            [*res.notes, f"model run failed: {type(exc).__name__}: {exc}"],
        )
        rec.end(stage, "error")
        return res
    rec.end(stage)
    res.decision = d
    review.save_decision(db, opts.season, d, settings.mode)
    res.decision_at = db.con.execute(
        "SELECT max(created_at) FROM decisions WHERE season = ? AND gw = ?", [opts.season, d.gw]
    ).fetchone()[0]

    mean = dict(zip(proj.meta["player_id"], proj.mean()[:, 0], strict=True))
    recent = {r["event"]: r.get("event_transfers_cost", 0) // 4 for r in history.get("current", [])}
    ctx = safety.Context(
        settings, state, pool[["player_id", "team_id", "position", "status", "chance"]], mean,
        bootstrap_next_gw=ev.id, recent_hits=recent,
        projected_share=len(proj.meta) / max(len(pool), 1), snapshot_age_hours=age,
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
    res.held = hold
    rec.start("execute")
    if not live_ok:  # never send moves built on the last deadline's squad
        res.outcome = Outcome("live", errors=["your current FPL team could not be read: "
                                              "nothing was sent"])  # fmt: skip
    else:
        # held moves wait for team news, but the squad already owned still gets its best XI
        # and captain now, so a missed later run cannot leave last week's lineup in place
        res.outcome = execute(
            lineup_only(d, state, proj) if hold else d,
            ctx,
            rules,
            fpl_id,
            price,
            entry,
            exchange=(lambda: Signed(tokens)) if tokens else exchange,
            http=http,
        )
    if hold:
        res.notes.append("held run: lineup and captain set for the squad you own; "
                         "transfers wait for the later run")  # fmt: skip
    o = res.outcome
    rec.end("execute", "error" if o and o.errors else "ok")
    # the squad FPL now holds is the bot's (after hand changes it stays yours all gameweek).
    # Only a run whose moves were read back from FPL and matched counts: a held, blocked,
    # failed or unverified run never does.
    if (o and o.mode == "live" and o.ok and o.verified is True and not o.blocked and not hand
            and not hold):  # fmt: skip
        review.save_decision(db, opts.season, d, DONE)
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


def write_outputs(out: Path, res: RunResult, mode: str) -> None:
    """gw<N>.md/.json for a run that planned; a run that stopped earlier writes
    run-<status>.md instead, so it never replaces the gameweek's plan for the bot."""
    out.mkdir(parents=True, exist_ok=True)
    if not res.decision:
        (out / f"run-{res.status}.md").write_text(res.report + "\n", encoding="utf-8")
        return
    (out / f"gw{res.gw}.md").write_text(res.report + "\n", encoding="utf-8")
    if res.decision:
        (out / f"gw{res.gw}.json").write_text(json.dumps({
            "mode": mode, "decision": review.decision_payload(res.decision),
            "outcome": res.outcome.__dict__ if res.outcome else None,
            "chips": res.decision.chip_report, "run_id": res.run_id}, indent=2,
            default=str), encoding="utf-8")  # fmt: skip


__all__ = ["RunOptions", "RunResult", "run_weekly", "format_report", "write_outputs"]
