"""Lessons: after each gameweek, find what went wrong, say how the next run avoids it, and keep
it in a memory the plan reads before it decides.

The review (`review.py`) scores the projections; this layer looks at the week as you lived it,
from FPL's public endpoints for your real team (including moves you made by hand) and the
decisions the bot recorded:

* transfers you had to make yourself, and moves the bot held for a later week;
* the captain against the best player in the XI;
* bench order (a benched player who outscored a starter he could have replaced) and goalkeeper;
* hits that did not pay;
* big hauls by players most of the top 10k own and you did not.

Each mistake is stored in the `lessons` table with the points it cost, what happened, the lesson
and, when there is one, the memory switch it feeds. `memory()` turns the recent lessons into small,
capped changes to the next plan:

* `act_now` (the bot held moves that then paid): banked free transfers are worth less and the
  robustness bar for this week's moves drops a little, so good moves are made now, not next week;
* `hits` (a hit that did not pay): a hit must buy more expected points;
* the projections of players most of the top 10k own are scaled by what they actually scored
  against what was projected, over every reviewed week (shrunk, capped at 10%).

A lesson fades by DECAY each gameweek and is renewed when the mistake repeats. Mistakes without a
switch (captain, bench, goalkeeper) are watched: three in the last six weeks are reported as
repeated, for a model change. Every switch applied is listed in the plan's report.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field, replace
from datetime import datetime
from typing import Any

import numpy as np
import pandas as pd

from fpl_agent.db.database import FootballDB, now
from fpl_agent.solver.planner import PlannerConfig

DECAY = 0.6  # a lesson's weight a gameweek later
ACTIVE = 0.2  # below this weight a lesson no longer changes the plan
BANK_CUT = 0.3  # at full weight, banked free transfers are worth 30% less
WIN_CUT = 0.05  # ... and this week's moves need to beat rolling in 5 points fewer worlds
HIT_EXTRA = 1.0  # at full weight, a hit must buy one more expected point
TEMPLATE = 50.0  # percent of the top-10k sample: "the field's core"
TEMPLATE_PRIOR = 150  # player-weeks of evidence to trust the template residual half way
TEMPLATE_CAP = 0.10
GAP = 3  # points a bench or goalkeeper swap must have gained to count as a mistake
CAPTAIN_GAP = 4  # extra points the best captain would have brought
HAUL = 10
WATCH_WEEKS, WATCH_REPEAT = 6, 3
RULES = {"act_now", "hits"}


@dataclass
class Mistake:
    kind: str
    points: float  # what it cost in hindsight (for a hand move that paid: what it gained)
    what: str
    lesson: str
    rule: str | None = None  # the memory switch it feeds; None = watched only


@dataclass
class Week:
    """One finished gameweek as you lived it, keyed by FPL element ids."""

    gw: int
    names: dict[int, str]
    position: dict[int, int]  # 1 GKP, 2 DEF, 3 MID, 4 FWD
    pts: dict[int, int]
    mins: dict[int, int]
    xi: list[int]  # after auto-subs
    bench: list[int]  # left on the bench, in order
    captain: int
    multiplier: int  # the captain's
    before: set[int]  # the squad at the previous deadline
    transfers: list[dict[str, Any]]  # this gameweek's, from FPL
    hits: int
    chip: str | None = None
    bot_ins: set[int] = field(default_factory=set)  # moves the bot made itself
    bot_plan: dict[str, Any] | None = None  # the bot's decision before your first hand move
    xp: dict[int, float] = field(default_factory=dict)
    field: dict[int, float] = field(default_factory=dict)  # top-10k ownership %
    expected: float | None = None  # the last recorded plan's expected points
    official: int | None = None

    def n(self, p: int) -> str:
        return self.names.get(p, f"#{p}")

    def px(self, p: int) -> str:
        """`Name 6` or `Name 6 (exp 4.1)`."""
        exp = f" (exp {self.xp[p]:.1f})" if p in self.xp else ""
        return f"{self.n(p)} {self.pts.get(p, 0)}{exp}"


# ---- finding mistakes ------------------------------------------------------------------------


def find_mistakes(w: Week) -> list[Mistake]:
    out = _hand_moves(w) + _deferred(w) + _captain(w) + _bench(w) + _keeper(w) + _hits(w)
    return out + _missed_hauls(w)


def _hand_moves(w: Week) -> list[Mistake]:
    hand = [t for t in w.transfers if t["element_in"] not in w.bot_ins]
    if not hand or w.chip in ("wildcard", "freehit"):
        return []
    ins = [t["element_in"] for t in hand]
    outs = [t["element_out"] for t in hand]
    gain = sum(w.pts.get(i, 0) for i in ins) - sum(w.pts.get(o, 0) for o in outs)
    moves = ", ".join(f"{w.n(o)} {w.pts.get(o, 0)} -> {w.n(i)} {w.pts.get(i, 0)}"
                      for o, i in zip(outs, ins, strict=True))  # fmt: skip
    plan = w.bot_plan or {}
    planned = {i for wk in plan.get("plan", []) for i in wk.get("in", [])}
    later = [w.n(i) for i in ins if i in planned]
    core = [f"{w.n(i)} {w.field[i]:.0f}%" for i in ins if w.field.get(i, 0.0) >= TEMPLATE]
    what = f"you made {len(hand)} transfer(s) by hand: {moves} ({gain:+d} this week)"
    if plan and not plan.get("transfers_in"):
        what += "; the bot's plan was to roll"
    if gain < 0:
        lesson = ("your moves lost points against keeping the old players this week; the bot's "
                  "hold was right this time, so nothing changes")  # fmt: skip
        return [Mistake("hand_transfers", float(gain), what, lesson)]
    why = []
    if later:
        why.append(f"the bot planned {', '.join(later)} for a later week anyway")
    if core:
        why.append(f"most of the top 10k own {', '.join(core)}")
    lesson = ("make good moves now instead of banking the transfer: banked transfers are "
              "worth less to the plan and the bar for this week's moves is lower")  # fmt: skip
    if why:
        lesson += " (" + "; ".join(why) + ")"
    return [Mistake("hand_transfers", float(gain), what, lesson, "act_now")]


def _deferred(w: Week) -> list[Mistake]:
    """The bot rolled this week while its plan bought players next week who scored now."""
    plan = w.bot_plan or {}
    if not plan or plan.get("transfers_in") or not plan.get("plan"):
        return []
    nxt = plan["plan"][0]
    hand_out = {t["element_out"] for t in w.transfers}
    pairs = [(o, i) for o, i in zip(nxt.get("out", []), nxt.get("in", []), strict=False)
             if o not in hand_out]  # fmt: skip
    start = set(w.xi)
    # what each held move would have added: his points if the seller started, else nothing sure
    gains = [(o, i, w.pts.get(i, 0) - w.pts.get(o, 0)) for o, i in pairs if o in start]
    gains = [g for g in gains if g[2] >= GAP]
    if not gains:
        return []
    total = sum(g[2] for g in gains)
    moves = ", ".join(f"{w.n(o)} {w.pts.get(o, 0)} -> {w.n(i)} {w.pts.get(i, 0)}"
                      for o, i, _ in gains)  # fmt: skip
    return [Mistake(
        "deferred_moves", float(total),
        f"the bot held {moves} for GW{nxt.get('gw', w.gw + 1)} and rolled the transfer",
        "a move the plan is going to make anyway is made this week when the transfer is free",
        "act_now",
    )]  # fmt: skip


def _captain(w: Week) -> list[Mistake]:
    played = [p for p in w.xi if w.mins.get(p, 0) > 0]
    if not played or w.captain not in w.pts:
        return []
    best = max(played, key=lambda p: w.pts.get(p, 0))
    regret = (w.pts.get(best, 0) - w.pts.get(w.captain, 0)) * (w.multiplier - 1)
    if best == w.captain or regret < CAPTAIN_GAP:
        return []
    opts = [o.get("player_id") for o in (w.bot_plan or {}).get("captain_options", [])]
    if best in opts:
        lesson = (
            f"{w.n(best)} was on the captain shortlist; the tie-break (haul chance and the "
            "field) chose otherwise. Watched: one week says little about the tie-break"
        )
    else:
        lesson = (f"{w.n(best)} was not on the captain shortlist, so his projection was too "
                  "low; watched for a repeat")  # fmt: skip
    return [Mistake("captain", float(regret),
                    f"captain {w.px(w.captain)} x{w.multiplier}; best in the XI was "
                    f"{w.px(best)}", lesson)]  # fmt: skip


FORMATION_MIN = {1: 1, 2: 3, 3: 2, 4: 1}
FORMATION_MAX = {1: 1, 2: 5, 3: 5, 4: 3}


def _bench(w: Week) -> list[Mistake]:
    """The best single legal swap of a starter for an outfield player left on the bench."""
    best = None
    count = {k: sum(1 for p in w.xi if w.position.get(p) == k) for k in FORMATION_MIN}
    for b in w.bench:
        if w.position.get(b) == 1 or w.mins.get(b, 0) == 0:
            continue
        for s in w.xi:
            ps, pb = w.position.get(s), w.position.get(b)
            legal = ps == pb or (count[ps] > FORMATION_MIN[ps] and count[pb] < FORMATION_MAX[pb])
            if ps == 1 or not legal:
                continue
            gain = w.pts.get(b, 0) - w.pts.get(s, 0)
            if gain >= GAP and (best is None or gain > best[2]):
                best = (s, b, gain)
    if not best:
        return []
    s, b, gain = best
    lesson = "bench order follows expected points"
    if s in w.xp and b in w.xp:
        lesson += (f": {w.n(b)} was expected {w.xp[b]:.1f} against {w.xp[s]:.1f}, so this is "
                   "the projection's miss, not the order's") if w.xp[b] < w.xp[s] else (
                   f": {w.n(b)} was expected more ({w.xp[b]:.1f} against {w.xp[s]:.1f}) and "
                   "still sat out, so the XI choice needs a look")  # fmt: skip
    return [Mistake("bench", float(gain),
                    f"{w.px(s)} started while {w.px(b)} was on the bench",
                    lesson + "; watched for a repeat")]  # fmt: skip


def _keeper(w: Week) -> list[Mistake]:
    gk = [p for p in w.xi if w.position.get(p) == 1]
    sub = [p for p in w.bench if w.position.get(p) == 1]
    if not gk or not sub or w.mins.get(sub[0], 0) == 0:
        return []
    gain = w.pts.get(sub[0], 0) - w.pts.get(gk[0], 0)
    if gain < GAP:
        return []
    return [Mistake("goalkeeper", float(gain),
                    f"{w.px(gk[0])} started in goal, {w.px(sub[0])} was on the bench",
                    "the starting keeper is the one expected to score more; watched: a keeper "
                    "swap pays only when the projection gap is real")]  # fmt: skip


def _hits(w: Week) -> list[Mistake]:
    if not w.hits or w.chip in ("wildcard", "freehit"):
        return []
    ins = [t["element_in"] for t in w.transfers]
    outs = [t["element_out"] for t in w.transfers]
    net = sum(w.pts.get(i, 0) for i in ins) - sum(w.pts.get(o, 0) for o in outs) - 4 * w.hits
    if net >= 0:
        return []
    by_bot = any(i in w.bot_ins for i in ins)
    return [Mistake("hits", float(-net),
                    f"-{4 * w.hits} in hits; the moves made {net + 4 * w.hits:+d} this week",
                    "a hit must buy more expected points" if by_bot
                    else "hits taken by hand: the bot's bar for hits stays as it is",
                    "hits" if by_bot else None)]  # fmt: skip


def _missed_hauls(w: Week) -> list[Mistake]:
    have = set(w.xi) | set(w.bench)
    miss = [p for p, v in w.pts.items()
            if v >= HAUL and p not in have and w.field.get(p, 0.0) >= TEMPLATE]  # fmt: skip
    if not miss:
        return []
    miss.sort(key=lambda p: -w.pts[p])
    return [Mistake("missed_template_haul", float(sum(w.pts[p] for p in miss)),
                    "hauls by players most of the top 10k own and you did not: "
                    + ", ".join(f"{w.px(p)}, {w.field[p]:.0f}% owned" for p in miss),
                    "the field's core is projected with the template correction learned from "
                    "every reviewed week")]  # fmt: skip


# ---- reading the week -----------------------------------------------------------------------


def _ts(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00")).replace(tzinfo=None)


def load_week(db: FootballDB, fpl, season: int, gw: int, entry: int) -> Week:
    raw = fpl.bootstrap_raw()
    names = {e["id"]: e.get("web_name", str(e["id"])) for e in raw.get("elements", [])}
    position = {e["id"]: int(e.get("element_type", 0)) for e in raw.get("elements", [])}
    live = fpl.live_raw(gw).get("elements", [])
    pts = {e["id"]: int(e["stats"]["total_points"]) for e in live}
    mins = {e["id"]: int(e["stats"].get("minutes", 0)) for e in live}
    picks = fpl.entry_picks_raw(entry, gw)
    rows = sorted(picks.get("picks", []), key=lambda r: r.get("position", 0))
    order = [r["element"] for r in rows]
    xi, bench = order[:11], order[11:]
    for s in picks.get("automatic_subs", []) or []:
        if s["element_out"] in xi and s["element_in"] in bench:
            xi[xi.index(s["element_out"])] = s["element_in"]
            bench.remove(s["element_in"])
    cap = next((r for r in rows if r.get("is_captain")), rows[0] if rows else {})
    prev = fpl.entry_picks_raw(entry, gw - 1) if gw > 1 else {"picks": []}
    hist = picks.get("entry_history") or {}
    week_moves = [t for t in fpl.entry_transfers_raw(entry) if t.get("event") == gw]
    to_fpl = _fpl_ids(db, season)
    runs = db.query("SELECT created_at, mode, payload FROM decisions WHERE season = ? AND gw = ? "
                    "ORDER BY created_at", [season, gw])  # fmt: skip
    plans = [(r.created_at, r.mode, _to_fpl(json.loads(r.payload), to_fpl))
             for r in runs.itertuples()]  # fmt: skip
    bot_ins = {i for _, mode, p in plans if mode == "live-done" for i in p.get("transfers_in", [])}
    hand_at = [
        _ts(t["time"]) for t in week_moves if t["element_in"] not in bot_ins and t.get("time")
    ]
    first_hand = min(hand_at, default=None)
    before = [p for at, _, p in plans if first_hand is None or at <= first_hand]
    xp = _expected(db, season, gw, to_fpl)
    fld = db.query("SELECT fpl_id, owned FROM field_ownership WHERE season = ? AND gw = ?",
                   [season, gw])  # fmt: skip
    mult = max(int(cap.get("multiplier", 2)), 3 if picks.get("active_chip") == "3xc" else 2)
    return Week(
        gw=gw, names=names, position=position, pts=pts, mins=mins, xi=xi, bench=bench,
        captain=cap.get("element", 0), multiplier=mult,
        before={r["element"] for r in prev.get("picks", [])}, transfers=week_moves,
        hits=int(hist.get("event_transfers_cost", 0)) // 4, chip=picks.get("active_chip"),
        bot_ins=bot_ins, bot_plan=before[-1] if before else None,
        xp={p: xp[p] for p in order if p in xp},
        field=dict(zip(fld["fpl_id"].astype(int), fld["owned"].astype(float), strict=True)),
        expected=plans[-1][2].get("xp") if plans else None, official=hist.get("points"),
    )  # fmt: skip


def _fpl_ids(db: FootballDB, season: int) -> dict[int, int]:
    rows = db.con.execute(
        "SELECT player_id, any_value(fpl_id) FROM fpl_players_gw "
        "WHERE season = ? AND player_id IS NOT NULL GROUP BY player_id",
        [season],
    ).fetchall()
    return {int(p): int(f) for p, f in rows}


def _to_fpl(payload: dict[str, Any], to_fpl: dict[int, int]) -> dict[str, Any]:
    """A decision payload with every player in FPL ids."""
    f = lambda ids: [to_fpl.get(int(i), int(i)) for i in ids]  # noqa: E731
    out = dict(payload)
    for k in ("transfers_in", "transfers_out", "xi", "bench"):
        out[k] = f(payload.get(k, []))
    out["plan"] = [{**wk, "in": f(wk.get("in", [])), "out": f(wk.get("out", []))}
                   for wk in payload.get("plan", [])]  # fmt: skip
    out["captain_options"] = [{**o, "player_id": to_fpl.get(int(o["player_id"]), o["player_id"])}
                              for o in payload.get("captain_options", [])]  # fmt: skip
    return out


def _expected(db: FootballDB, season: int, gw: int, to_fpl: dict[int, int]) -> dict[int, float]:
    proj = db.query(
        "SELECT player_id, xpts FROM projections WHERE season = ? AND gw = ? "
        "QUALIFY created_at = max(created_at) OVER (PARTITION BY player_id)",
        [season, gw],
    )
    return {to_fpl[int(p)]: float(x) for p, x in zip(proj["player_id"], proj["xpts"], strict=True)
            if int(p) in to_fpl}  # fmt: skip


# ---- storing and reporting ------------------------------------------------------------------


def save(db: FootballDB, season: int, gw: int, mistakes: list[Mistake]) -> None:
    """Replace the gameweek's lessons (a re-run with later data corrects them)."""
    db.con.execute("DELETE FROM lessons WHERE season = ? AND gw = ?", [season, gw])
    db.append("lessons", pd.DataFrame([{
        "season": season, "gw": gw, "kind": m.kind, "points": m.points, "what": m.what,
        "lesson": m.lesson, "rule": m.rule, "created_at": now(),
    } for m in mistakes]))  # fmt: skip


def report(w: Week, mistakes: list[Mistake], done: bool = True) -> str:
    head = f"GW{w.gw} lessons"
    lines = [head if done else f"{head} (PROVISIONAL: the gameweek is not finished)"]
    if w.official is not None:
        exp = f", the plan expected {w.expected:.1f} before hits" if w.expected else ""
        lines.append(f"{w.official} points{exp}")
    if not mistakes:
        lines.append("no mistakes found")
    for m in mistakes:
        tag = f"memory: {m.rule}" if m.rule else "watched"
        lines += [f"- {m.what} [{m.points:+.0f} pts]", f"  lesson: {m.lesson} ({tag})"]
    return "\n".join(lines)


def run(db: FootballDB, fpl, season: int, gw: int, entry: int) -> str:
    """Find, store and report the gameweek's mistakes."""
    w = load_week(db, fpl, season, gw, entry)
    ev = next((e for e in fpl.bootstrap_raw().get("events", []) if e["id"] == gw), {})
    mistakes = find_mistakes(w)
    save(db, season, gw, mistakes)
    return report(w, mistakes, done=bool(ev.get("finished", True)))


# ---- the memory the next plan reads ---------------------------------------------------------


@dataclass
class Memory:
    weights: dict[str, float] = field(default_factory=dict)  # rule -> weight in [0, 1]
    template: float = 1.0  # multiplier on the field's core projections
    template_n: int = 0
    template_used: int = 0  # players the correction was applied to in this run
    repeated: dict[str, int] = field(default_factory=dict)  # watched kind -> weeks seen
    lessons: list[str] = field(default_factory=list)  # the active lessons, newest first

    def w(self, rule: str) -> float:
        v = self.weights.get(rule, 0.0)
        return v if v >= ACTIVE else 0.0

    def planner(self, cfg: PlannerConfig) -> PlannerConfig:
        a, h = self.w("act_now"), self.w("hits")
        if a:
            cfg = replace(cfg, ft_values=tuple(v * (1 - BANK_CUT * a) for v in cfg.ft_values))
        if h:
            cfg = replace(cfg, hit_buffer=cfg.hit_buffer + HIT_EXTRA * h)
        return cfg

    def min_win(self, base: float) -> float:
        return base - WIN_CUT * self.w("act_now")

    def apply_template(self, proj, owned: dict[int, float]) -> list[int]:
        """Scale the sampled points of the field's core (canonical ids, top-10k %) in place."""
        if self.template == 1.0 or not owned:
            return []
        hit = []
        for i, pid in enumerate(proj.player_ids):
            if owned.get(int(pid), 0.0) >= TEMPLATE:
                proj.points[i] *= np.float32(self.template)
                hit.append(int(pid))
        self.template_used = len(hit)
        return hit

    def notes(self) -> list[str]:
        out = []
        a, h = self.w("act_now"), self.w("hits")
        if a:
            out.append(f"memory: act now (weight {a:.2f}): banked transfers valued "
                       f"x{1 - BANK_CUT * a:.2f}, this week's moves need {WIN_CUT * a:.0%} fewer "
                       "winning worlds")  # fmt: skip
        if h:
            out.append(f"memory: hits (weight {h:.2f}): a hit must buy "
                       f"{HIT_EXTRA * h:+.1f} more expected points")  # fmt: skip
        if self.template_used:
            out.append(
                f"memory: the field's core (over {TEMPLATE:.0f}% of the top 10k) projected "
                f"x{self.template:.3f}, learned from {self.template_n} player-weeks "
                f"({self.template_used} players)"
            )
        out += [f"memory: lesson from {x}" for x in self.lessons[:3]]
        for kind, n in sorted(self.repeated.items()):
            out.append(f"memory: repeated mistake, {kind} in {n} of the last {WATCH_WEEKS} "
                       "gameweeks: needs a model change")  # fmt: skip
        return out


def memory(db: FootballDB, season: int, before_gw: int) -> Memory:
    """What the plan for `before_gw` learns from the lessons of the gameweeks before it."""
    rows = db.query("SELECT gw, kind, rule, lesson FROM lessons WHERE season = ? AND gw < ? "
                    "ORDER BY gw DESC", [season, before_gw])  # fmt: skip
    mem = Memory()
    seen: dict[str, set[int]] = {}
    for r in rows.itertuples():
        age = before_gw - 1 - int(r.gw)
        if isinstance(r.rule, str) and r.rule in RULES:
            mem.weights[r.rule] = min(1.0, mem.weights.get(r.rule, 0.0) + DECAY**age)
            if DECAY**age >= ACTIVE:
                mem.lessons.append(f"GW{r.gw}: {r.lesson}")
        elif age < WATCH_WEEKS:
            seen.setdefault(r.kind, set()).add(int(r.gw))
    mem.repeated = {k: len(v) for k, v in seen.items() if len(v) >= WATCH_REPEAT}
    mem.template, mem.template_n = template_factor(db, season, before_gw)
    return mem


def template_factor(db: FootballDB, season: int, before_gw: int) -> tuple[float, int]:
    """Actual over projected points of the field's core across reviewed weeks, shrunk toward 1
    and capped, with the player-weeks behind it."""
    rows = db.query("SELECT payload FROM reviews WHERE season = ? AND kind = 'prediction' "
                    "AND gw < ?", [season, before_gw])  # fmt: skip
    pred = act = 0.0
    n = 0
    for blob in rows["payload"]:
        t = json.loads(blob).get("template") or {}
        if t.get("n"):
            pred += t["mean_pred"] * t["n"]
            act += t["mean_actual"] * t["n"]
            n += t["n"]
    if not n or pred <= 0:
        return 1.0, 0
    w = n / (n + TEMPLATE_PRIOR)
    return float(np.clip(1.0 + w * (act / pred - 1.0), 1 - TEMPLATE_CAP, 1 + TEMPLATE_CAP)), n
