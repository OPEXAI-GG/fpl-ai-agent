"""Scout: reads FPL's injury/news text and returns a probability that each player plays.

It is a bounded helper, not a decision maker:
* at most one Claude API call per run, at most `limit` players (your squad first, then the
  priciest), news text and total input capped, a small output cap;
* players FPL lists as left/unavailable (n/u) are never sent and never changed;
* the answer is validated; malformed, unknown or duplicate ids are dropped deterministically;
* it can only move a player's availability within +-`max_shift` of FPL's own flag;
* answers are cached per player under everything that went into them (model, prompt version,
  season, gameweek, decision date, status, chance and the news text), so an unchanged picture
  costs no call; every call is recorded (`scout_calls`) with its usage, stop reason, latency
  and outcome, so a failed, empty or truncated answer is visible rather than looking like
  "nothing to change";
* with no `ANTHROPIC_API_KEY`, or on any failure, the pool is returned unchanged.
The adjusted `chance` feeds the minutes model's availability overlay, nothing else.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import time
from dataclasses import dataclass, field
from datetime import timedelta

import httpx
import pandas as pd

log = logging.getLogger(__name__)
URL = "https://api.anthropic.com/v1/messages"
DEFAULT_MODEL = "claude-sonnet-5-5"
PROMPT_VERSION = 2
NEWS_CHARS = 300  # per player
INPUT_BYTES = 12_000  # the whole player list
MAX_TOKENS = 1500
CACHE_HOURS = 12.0

SYSTEM = (
    "You assess Premier League injury and team news for Fantasy Premier League. For each player "
    "you are given FPL's status flag, its chance-of-playing number and its news text. Return ONLY "
    'a JSON list: [{"id": <int>, "p_play": <0..1 probability he plays real minutes next '
    'gameweek>, "reason": "<8 words>"}]. Be conservative: use only the text given, never invent '
    "facts. If the text gives no information beyond the flag, repeat the flag's implied value. "
    "The first line gives today's date and the gameweek's first kickoff: judge return dates "
    "against them."
)


@dataclass
class ScoutReport:
    """What the scout did this run, for the report and the call log."""

    outcome: str  # disabled | no_news | cached | ok | empty | truncated | parse_error | failed
    eligible: int = 0
    cached: int = 0
    sent: int = 0
    accepted: int = 0
    rejected: int = 0
    input_bytes: int = 0
    input_tokens: int | None = None
    output_tokens: int | None = None
    stop_reason: str | None = None
    latency_ms: int | None = None
    extra: dict = field(default_factory=dict)

    def note(self) -> str:
        if self.outcome == "disabled":
            return "scout skipped: no ANTHROPIC_API_KEY"
        if self.outcome == "no_news":
            return "scout: no flagged player has news text (nothing to read)"
        head = (f"scout: {self.eligible} with news, {self.cached} from cache, "
                f"{self.sent} sent, {self.accepted} accepted")  # fmt: skip
        if self.outcome in ("ok", "cached"):
            return head
        problem = self.outcome.replace("_", " ")
        return f"{head}; PROBLEM: {problem} (availability left as FPL has it)"


@dataclass(frozen=True)
class Adjustment:
    player_id: int
    p_play: float
    reason: str


class Scout:
    def __init__(
        self,
        key: str | None = None,
        client: httpx.Client | None = None,
        model: str | None = None,
        limit: int = 40,
        max_shift: float = 0.35,
    ) -> None:
        self.key = key or os.environ.get("ANTHROPIC_API_KEY") or None
        self.model = model or os.environ.get("SCOUT_MODEL", DEFAULT_MODEL)
        self.limit, self.max_shift = limit, max_shift
        self._http = client or httpx.Client(timeout=60)

    @property
    def enabled(self) -> bool:
        return bool(self.key)

    def select(self, pool: pd.DataFrame, squad: set[int] | frozenset[int] = frozenset()
               ) -> pd.DataFrame:  # fmt: skip
        """Players whose news could change a decision: flagged and carrying news text, never
        those FPL already rules out (n/u: nothing to adjust). Your squad first, then by price."""
        news = pool["news"].fillna("").astype(str).str.strip() != ""
        flagged = (pool["status"] != "a") | (pool["chance"].fillna(100) < 100)
        sel = pool[news & flagged & ~pool["status"].isin(["n", "u"])]
        sel = sel.assign(_own=~sel["player_id"].astype(int).isin(set(squad)))
        sel = sel.sort_values(["_own", "price", "player_id"], ascending=[True, False, True])
        return sel.drop(columns="_own").head(self.limit)

    def _key(self, r, ctx: dict) -> str:
        parts = [self.model, PROMPT_VERSION, ctx.get("season"), ctx.get("gw"), ctx.get("date"),
                 int(r.player_id), r.status, None if pd.isna(r.chance) else float(r.chance),
                 str(r.news)[:NEWS_CHARS]]  # fmt: skip
        return hashlib.sha256(json.dumps(parts, default=str).encode()).hexdigest()

    def payload(self, items: pd.DataFrame, ctx: dict) -> tuple[str, list[int]]:
        """The user message (one JSON object per line, under INPUT_BYTES) and the ids in it."""
        head = json.dumps({"today": ctx.get("date"), "first_kickoff": ctx.get("first_kickoff"),
                           "gameweek": ctx.get("gw")})  # fmt: skip
        lines, ids, size = [head], [], len(head)
        for r in items.itertuples():
            chance = None if pd.isna(r.chance) else float(r.chance)
            line = json.dumps({"id": int(r.player_id), "status": str(r.status), "chance": chance,
                               "news": str(r.news)[:NEWS_CHARS]})  # fmt: skip
            if size + len(line) + 1 > INPUT_BYTES:
                break
            lines.append(line)
            ids.append(int(r.player_id))
            size += len(line) + 1
        return "\n".join(lines), ids

    def ask(self, items: pd.DataFrame, ctx: dict | None = None,
            report: ScoutReport | None = None) -> list[Adjustment]:  # fmt: skip
        report = report if report is not None else ScoutReport("ok")
        if items.empty or not self.enabled:
            return []
        text_in, ids = self.payload(items, ctx or {})
        report.sent, report.input_bytes = len(ids), len(text_in.encode())
        body = {"model": self.model, "max_tokens": MAX_TOKENS, "system": SYSTEM,
                "messages": [{"role": "user", "content": text_in}]}  # fmt: skip
        t0 = time.perf_counter()
        try:
            r = self._http.post(URL, json=body, headers={
                "x-api-key": self.key, "anthropic-version": "2023-06-01"})  # fmt: skip
            r.raise_for_status()
            reply = r.json()
            text = "".join(b.get("text", "") for b in reply.get("content", []))
        except (httpx.HTTPError, ValueError) as exc:
            log.warning("scout call failed: %s", type(exc).__name__)
            report.outcome = "failed"
            report.extra["error"] = type(exc).__name__
            return []
        finally:
            report.latency_ms = int((time.perf_counter() - t0) * 1000)
        usage = reply.get("usage") or {}
        report.input_tokens = usage.get("input_tokens")
        report.output_tokens = usage.get("output_tokens")
        report.stop_reason = reply.get("stop_reason")
        out, rejected = parse_report(text, set(ids))
        report.accepted, report.rejected = len(out), rejected
        if report.stop_reason == "max_tokens":
            report.outcome = "truncated"
        elif not text.strip():
            report.outcome = "empty"
        elif not out and ids:
            report.outcome = "parse_error"
        return out

    def apply(self, pool: pd.DataFrame, adjustments: list[Adjustment]) -> pd.DataFrame:
        """Copy of `pool` with `chance` moved (bounded) toward the scout's view."""
        out = pool.copy()
        by_id = {a.player_id: a for a in adjustments}
        for idx, row in out.iterrows():
            a = by_id.get(int(row["player_id"]))
            if a is None or row["status"] in ("n", "u"):
                continue
            base = 1.0 if pd.isna(row["chance"]) else float(row["chance"]) / 100.0
            p = min(max(a.p_play, base - self.max_shift), base + self.max_shift)
            out.at[idx, "chance"] = round(100 * min(max(p, 0.0), 1.0), 1)
            if out.at[idx, "status"] == "a" and p < 1.0:
                out.at[idx, "status"] = "d"
        return out

    def run(self, pool: pd.DataFrame, db=None, ctx: dict | None = None,
            squad: set[int] | frozenset[int] = frozenset()
            ) -> tuple[pd.DataFrame, list[Adjustment]]:  # fmt: skip
        """The pool with availability adjusted. The run's ScoutReport is kept as
        `self.last_report`; with `db`, answers are cached and the call is logged."""
        ctx = ctx or {}
        if not self.enabled:
            self.last_report = ScoutReport("disabled")
            return pool, []
        items = self.select(pool, squad)
        report = ScoutReport("ok", eligible=len(items))
        self.last_report = report
        if items.empty:
            report.outcome = "no_news"
            return pool, []
        keys = {int(r.player_id): self._key(r, ctx) for r in items.itertuples()}
        hits = _cached(db, keys) if db is not None else {}
        report.cached = len(hits)
        todo = items[~items["player_id"].astype(int).isin(hits)]
        fresh = self.ask(todo, ctx, report) if not todo.empty else []
        if todo.empty:
            report.outcome = "cached"
        if db is not None:
            _store(db, fresh, keys, ctx, report, self.model)
        adj = sorted([*hits.values(), *fresh], key=lambda a: a.player_id)
        return (self.apply(pool, adj) if adj else pool), adj


def parse(text: str, allowed: set[int]) -> list[Adjustment]:
    """Strictly validate the model's JSON; unknown ids and out-of-range values are dropped."""
    return parse_report(text, allowed)[0]


def parse_report(text: str, allowed: set[int]) -> tuple[list[Adjustment], int]:
    """(accepted adjustments, number of rejected items). Unknown or repeated ids (the first
    answer for an id wins), malformed items and out-of-range values are rejected."""
    m = re.search(r"\[.*\]", text, re.S)
    if not m:
        return [], 0
    try:
        raw = json.loads(m.group(0))
    except ValueError:
        return [], 0
    out, seen, rejected = [], set(), 0
    for item in raw if isinstance(raw, list) else []:
        try:
            pid, p = int(item["id"]), float(item["p_play"])
        except (KeyError, TypeError, ValueError):
            rejected += 1
            continue
        if pid in allowed and pid not in seen and 0.0 <= p <= 1.0:
            seen.add(pid)
            out.append(Adjustment(pid, p, str(item.get("reason", ""))[:80]))
        else:
            rejected += 1
    return out, rejected


def _cached(db, keys: dict[int, str]) -> dict[int, Adjustment]:
    from fpl_agent.db.database import now

    if not keys:
        return {}
    marks = ", ".join("?" for _ in keys)
    rows = db.con.execute(
        f"SELECT key, player_id, p_play, reason FROM scout_cache WHERE key IN ({marks}) "
        "AND expires_at > ?",
        [*keys.values(), now()],
    ).fetchall()
    return {int(pid): Adjustment(int(pid), float(p), reason or "") for _, pid, p, reason in rows}


def _store(db, fresh: list[Adjustment], keys: dict[int, str], ctx: dict,
           report: ScoutReport, model: str) -> None:  # fmt: skip
    from fpl_agent.db.database import now

    stamp = now()
    if fresh and report.outcome == "ok":  # only a complete, valid answer is reused later
        db.upsert("scout_cache", pd.DataFrame([{
            "key": keys[a.player_id], "player_id": a.player_id, "p_play": a.p_play,
            "reason": a.reason, "created_at": stamp,
            "expires_at": stamp + timedelta(hours=CACHE_HOURS)} for a in fresh]))  # fmt: skip
    db.append("scout_calls", pd.DataFrame([{
        "called_at": stamp, "model": model, "prompt_version": PROMPT_VERSION,
        "season": ctx.get("season"), "gw": ctx.get("gw"), "eligible": report.eligible,
        "cached": report.cached, "sent": report.sent, "accepted": report.accepted,
        "rejected": report.rejected, "input_bytes": report.input_bytes,
        "input_tokens": report.input_tokens, "output_tokens": report.output_tokens,
        "stop_reason": report.stop_reason, "latency_ms": report.latency_ms,
        "outcome": report.outcome}]))  # fmt: skip
