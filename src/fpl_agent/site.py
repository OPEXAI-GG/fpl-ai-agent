"""Plans for the FPL Sim website's subscribers: the shared part once, then one plan per manager.

The website (web/) keeps the accounts; this module asks it which FPL manager ids to plan for,
runs the expensive shared work once (projection, ownership, scout, odds), then for each manager
reads their public squad and makes either the free pick (captain, vice, expected points) or the
full plan (`decide`: transfers, chips, XI, bench, six-week horizon). Results go back to the site
over its engine API, authenticated with ENGINE_TOKEN.

Advice only: nothing here logs in to FPL or touches anyone's team, and your own weekly run
(`agent.run_weekly`, the plan workflow) is not involved.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import httpx

from fpl_agent.agent import _names, _ownership
from fpl_agent.data import oddsapi
from fpl_agent.data.calendar import hours_to_deadline, in_action_window, next_event
from fpl_agent.data.entry import parse_squad
from fpl_agent.data.fpl_client import FPLClient
from fpl_agent.db.database import FootballDB
from fpl_agent.model import calibration, project
from fpl_agent.model.project import Projection
from fpl_agent.model.simulate import SimConfig
from fpl_agent.rules import Rules
from fpl_agent.scout import Scout
from fpl_agent.solver.decide import Decision, captain_choice, decide, rank_risk
from fpl_agent.solver.inputs import to_canonical
from fpl_agent.solver.planner import FORMATION_MAX, FORMATION_MIN, PlannerConfig

log = logging.getLogger(__name__)

# The site plans from three days out (so subscribers see a plan early in the week) to 45 minutes
# before the deadline. A full plan is re-made once it is MIN_AGE_HOURS old, and on every run in
# the last FINAL_HOURS, when the team news is in.
WINDOW_HOURS = 72.0
MIN_AGE_HOURS = 8.0
FINAL_HOURS = 5.0
TOP_CAPTAINS = 8
BATCH = 25
UNREADABLE = "We could not read this FPL team. Check the manager ID."


class SiteClient:
    """The website's engine API: who to plan for, and where the plans go."""

    def __init__(self, base_url: str, token: str, client: httpx.Client | None = None) -> None:
        self._base = base_url.rstrip("/")
        self._http = client or httpx.Client(timeout=30)
        self._auth = {"Authorization": f"Bearer {token}", "User-Agent": "fpl-agent-site/0.1"}

    def managers(self, gw: int) -> list[dict[str, Any]]:
        r = self._http.get(f"{self._base}/api/engine/managers", params={"gw": gw},
                           headers=self._auth)  # fmt: skip
        r.raise_for_status()
        return r.json()["managers"]

    def post(self, path: str, body: dict[str, Any]) -> None:
        r = self._http.post(f"{self._base}{path}", json=body, headers=self._auth)
        r.raise_for_status()

    @classmethod
    def from_env(cls) -> SiteClient | None:
        url, token = os.environ.get("SITE_URL", ""), os.environ.get("ENGINE_TOKEN", "")
        return cls(url, token) if url and token else None


@dataclass
class SyncOptions:
    season: int = 2026
    horizon: int = 6
    samples: int = 1000
    force: bool = False  # skip the window gate and re-plan everyone
    limit: int | None = None  # at most this many full plans per run


@dataclass
class SyncResult:
    status: str  # "ok" | "skipped" | "error"
    gw: int | None = None
    full: int = 0
    free: int = 0
    kept: int = 0  # full plans still fresh, left as they were
    failed: int = 0
    notes: list[str] = field(default_factory=list)

    @property
    def exit_code(self) -> int:
        return 2 if self.status == "error" else 0

    def report(self) -> str:
        head = f"site sync GW{self.gw}: {self.status}"
        counts = (f"{self.full} full plans, {self.free} free picks, {self.kept} kept, "
                  f"{self.failed} failed")  # fmt: skip
        return "\n".join([head, counts, *[f"note: {n}" for n in self.notes]])


def labels(pool, raw: dict[str, Any], names: dict[int, str]) -> dict[int, dict[str, Any]]:
    """Canonical player id -> what the site shows: name, club, position, price (£m)."""
    teams = {t["id"]: t.get("short_name", str(t["id"])) for t in raw.get("teams", [])}
    els = {e["id"]: e for e in raw.get("elements", [])}
    out = {}
    for r in pool.itertuples():
        e = els.get(int(r.fpl_id), {})
        pid = int(r.player_id)
        out[pid] = {
            "id": pid, "name": e.get("web_name") or names.get(pid, str(pid)),
            "team": teams.get(e.get("team"), ""), "pos": r.position,
            "price": round(int(r.price) / 10, 1),
        }  # fmt: skip
    return out


def best_xi(proj: Projection, squad_ids: list[int], g: int = 0) -> list[int]:
    """The XI with the most expected points this week from a fixed squad, in a legal formation."""
    mean = proj.mean()
    pos = dict(zip(proj.meta["player_id"], proj.meta["position"], strict=True))
    rows = {pid: i for i, pid in enumerate(proj.player_ids)}
    ids = [p for p in squad_ids if p in rows]
    xp = {p: float(mean[rows[p], g]) for p in ids}
    chosen: list[int] = []
    for p in ("GKP", "DEF", "MID", "FWD"):
        chosen += sorted((i for i in ids if pos[i] == p), key=lambda i: -xp[i])[: FORMATION_MIN[p]]
    for i in sorted((i for i in ids if i not in chosen and pos[i] != "GKP"), key=lambda i: -xp[i]):
        if len(chosen) == 11:
            break
        if sum(1 for c in chosen if pos[c] == pos[i]) < FORMATION_MAX[pos[i]]:
            chosen.append(i)
    return chosen


class _Show:
    """Player ids -> labelled dicts with this week's expected points."""

    def __init__(self, lab: dict[int, dict[str, Any]], proj: Projection) -> None:
        self.lab = lab
        self.xp = dict(zip(proj.player_ids, proj.mean()[:, 0].tolist(), strict=True))

    def __call__(self, pid: int) -> dict[str, Any]:
        base = self.lab.get(pid, {"id": pid, "name": str(pid), "team": "", "pos": "", "price": 0})
        return {**base, "xp": round(self.xp.get(pid, 0.0), 2)}


def free_pick(proj: Projection, squad_ids: list[int], show: _Show, gw: int) -> dict[str, Any]:
    xi = best_xi(proj, squad_ids)
    cap, vice, opts = captain_choice(proj, xi)
    xp = sum(show.xp.get(p, 0.0) for p in xi) + show.xp.get(cap, 0.0)
    return {
        "kind": "free", "gw": gw, "captain": show(cap), "vice": show(vice), "xp": round(xp, 1),
        "captain_options": [{**show(o["player_id"]), "p_haul": round(o["p_haul"], 3)}
                            for o in opts],
    }  # fmt: skip


def full_plan(d: Decision, show: _Show) -> dict[str, Any]:
    pairs = lambda ins, outs: [  # noqa: E731
        {"out": show(o), "in": show(i)} for i, o in zip(ins, outs, strict=False)
    ]
    return {
        "kind": "full", "gw": d.gw, "action": d.action, "chip": d.chip,
        "xp": round(d.xp, 1), "xp_no_transfers": round(d.xp_no_transfers, 1), "hits": d.hits,
        "bank_after": round(d.bank_after / 10, 1), "ft_after": d.ft_after,
        "transfers": pairs(d.transfers_in, d.transfers_out),
        "captain": show(d.captain), "vice": show(d.vice),
        "captain_options": [{**show(o["player_id"]), "p_haul": round(o["p_haul"], 3)}
                            for o in d.captain_options],
        "xi": [show(p) for p in d.xi], "bench": [show(p) for p in d.bench],
        "held": d.chip_report.get("held", {}), "warnings": d.warnings,
        "field": d.field_view, "checks": d.checks,
        "horizon": [{"gw": w.gw, "chip": w.chip, "xp": round(w.xp, 1), "hits": w.hits,
                     "free_transfers": w.ft_before, "captain": show(w.captain)["name"],
                     "transfers": pairs(w.transfers_in, w.transfers_out)}
                    for w in d.horizon[1:]],
    }  # fmt: skip


def top_captains(proj: Projection, show: _Show, pool, n: int = TOP_CAPTAINS) -> list[dict]:
    """The week's best captains across the whole game: the site's free public table."""
    ok = set(pool.loc[pool["status"] == "a", "player_id"].astype(int))
    haul = (proj.points[:, 0, :] >= 10).mean(axis=1)
    owned = dict(zip(pool["player_id"].astype(int), pool["owned"].fillna(0).astype(float),
                     strict=True)) if "owned" in pool else {}  # fmt: skip
    rows = [(p, show.xp[p], float(haul[i])) for i, p in enumerate(proj.player_ids) if p in ok]
    rows.sort(key=lambda r: -r[1])
    return [{**show(p), "p_haul": round(h, 3), "owned": round(owned.get(p, 0.0), 1)}
            for p, _, h in rows[:n]]  # fmt: skip


def _fresh(m: dict[str, Any], now: datetime, hours_left: float) -> bool:
    """A full plan made recently enough to keep (never in the last hours before the deadline)."""
    at = m.get("planned_at")
    if not at or m.get("kind") != "full" or hours_left <= FINAL_HOURS:
        return False
    age = (now - datetime.fromtimestamp(int(at), UTC)).total_seconds() / 3600
    return age < MIN_AGE_HOURS


def sync(
    db: FootballDB,
    site: SiteClient,
    opts: SyncOptions,
    fpl: FPLClient | None = None,
    scout: Scout | None = None,
    odds: oddsapi.OddsClient | None = None,
    now: datetime | None = None,
) -> SyncResult:
    now = now or datetime.now(UTC)
    res = SyncResult("ok")
    fpl = fpl or FPLClient()
    try:
        bs, raw = fpl.bootstrap(), fpl.bootstrap_raw()
        ev = next_event(bs)
    except Exception as exc:  # noqa: BLE001
        res.status = "error"
        res.notes.append(f"could not read FPL bootstrap: {type(exc).__name__}")
        return res
    if ev is None:
        res.status = "skipped"
        res.notes.append("no upcoming gameweek")
        return res
    res.gw = ev.id
    left = hours_to_deadline(ev, now)
    if not opts.force and not in_action_window(ev, now, hi_hours=WINDOW_HOURS):
        res.status = "skipped"
        res.notes.append(f"deadline is {left:.0f}h away, outside the site's window")
        return res
    try:
        managers = site.managers(ev.id)
    except Exception as exc:  # noqa: BLE001
        res.status = "error"
        res.notes.append(f"could not read the site's managers: {type(exc).__name__}")
        return res

    rules = Rules.nearest(opts.season)
    pool = project.current_pool(db, opts.season, ev.id)
    if pool.empty:
        res.status = "error"
        res.notes.append(f"database has no player snapshot for GW{ev.id}: run db-update")
        return res
    odds = odds or oddsapi.OddsClient()
    if odds.enabled:
        try:
            res.notes.append(f"odds: {oddsapi.load_events(db, odds.upcoming())} matches priced")
        except Exception as exc:  # noqa: BLE001
            res.notes.append(f"odds skipped ({type(exc).__name__})")
    scout = scout or Scout()
    if scout.enabled:
        pool, adj = scout.run(pool)
        res.notes.append(f"scout adjusted {len(adj)} players")
    own, eo, own_note = _ownership(db, fpl, opts.season, ev.id, pool)
    res.notes.append(own_note)
    gws = list(range(ev.id, min(ev.id + opts.horizon, 39)))
    try:
        proj = project.build_projection(db, opts.season, gws, rules=rules, pool=pool,
                                        cfg=SimConfig(n=opts.samples), pool_gw=ev.id)  # fmt: skip
        fac = calibration.factors(db, opts.season, ev.id)
        if fac:
            calibration.apply(proj, fac)
    except Exception as exc:  # noqa: BLE001
        log.exception("projection failed")
        res.status = "error"
        res.notes.append(f"projection failed: {type(exc).__name__}: {exc}")
        return res
    names = _names(db)
    show = _Show(labels(pool, raw, names), proj)
    site.post("/api/engine/gameweek", {
        "gw": ev.id, "deadline": ev.deadline_time.isoformat(),
        "captains": top_captains(proj, show, pool),
    })  # fmt: skip

    cfg = PlannerConfig(risk_lambda=rank_risk(ev.id) if eo else 0.0)
    out: list[dict[str, Any]] = []
    full_made = 0
    for m in managers:
        mid = int(m["manager_id"])
        pro = m.get("tier") == "pro"
        if pro and not opts.force and _fresh(m, now, left):
            res.kept += 1
            continue
        try:
            picks = fpl.entry_picks_raw(mid, ev.id - 1) if ev.id > 1 else {"picks": []}
            history = fpl.entry_history_raw(mid)
            state = parse_squad(picks, fpl.entry_transfers_raw(mid), history, raw, ev.id, rules)
            state = to_canonical(state, pool)
            ids = [p.player_id for p in state.squad]
            if pro and (opts.limit is None or full_made < opts.limit):
                d = decide(state, proj, rules, names=names, cfg=cfg, ownership=own, eo=eo)
                payload = full_plan(d, show)
                full_made += 1
                res.full += 1
            else:
                payload = free_pick(proj, ids, show, ev.id)
                res.free += 1
        except Exception as exc:  # noqa: BLE001  (one manager's failure never stops the rest)
            log.warning("manager %s failed: %s", mid, type(exc).__name__)
            payload = {"kind": "error", "gw": ev.id, "reason": UNREADABLE}
            res.failed += 1
        out.append({"manager_id": mid, "gw": ev.id, "payload": payload})
        if len(out) >= BATCH:
            site.post("/api/engine/plans", {"plans": out})
            out = []
    if out:
        site.post("/api/engine/plans", {"plans": out})
    return res


__all__ = ["SiteClient", "SyncOptions", "SyncResult", "sync", "best_xi", "free_pick", "full_plan"]
