"""News evidence -> one bounded availability signal per player, as known at a cutoff.

Eligibility of a claim at cutoff `c` (all must hold, otherwise it changes nothing):

* about a resolved player with name-match confidence >= 0.7 (ambiguous names never count);
* kind availability, training or return;
* `effective_at <= c` (we had observed it; the publication time is irrelevant);
* from the newest revision of its item observed by `c` (an edit replaces, never adds);
* `expires_at > c` (fresh).

Signal per claim (probability of playing next gameweek): out 0.0, doubtful 0.5, fit 0.95,
training full 0.9, training partial 0.6; a return claim sets 0 for fixtures before its return
date. Weight = tier (official 1.0, outlet/journalist 0.7, other 0.4) x certainty (confirmed 1.0,
speculative 0.4) x name confidence.

Copies count once: claims are grouped by story (`story_key`) and each story keeps its newest
claim. The newest story decides; a second independent story agreeing within 0.2 adds half its
weight (capped at 1). Two confirmed stories from the last 48 hours that disagree by 0.5 or more
are a **conflict**: no change. The result is stored per player with the claim ids and quotes,
so every change can be explained and traced back to its sources.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta

import pandas as pd

from fpl_agent.db.database import FootballDB

MIN_ENTITY = 0.7
TIER_W = {1: 1.0, 2: 0.7, 3: 0.4}
CERT_W = {"confirmed": 1.0, "speculative": 0.4}
SIGNAL = {("availability", "out"): 0.0, ("availability", "doubtful"): 0.5,
          ("availability", "fit"): 0.95, ("training", "full"): 0.9, ("training", "partial"): 0.6}  # fmt: skip
CONFLICT_WINDOW = timedelta(hours=48)


@dataclass
class PlayerEvidence:
    player_id: int
    state: str  # applied | conflict | none
    p_play: float | None = None
    weight: float = 0.0
    return_at: datetime | None = None
    claims: list[dict] = field(default_factory=list)  # the supporting claims, for explanations
    stories: int = 0
    note: str = ""


def eligible_claims(db: FootballDB, cutoff: datetime) -> pd.DataFrame:
    return db.query(
        "WITH latest AS (SELECT item_id, max(revision) AS revision FROM news_revisions "
        "WHERE observed_at <= ? GROUP BY item_id) "
        "SELECT c.* FROM evidence_claims c JOIN latest l ON l.item_id = c.item_id "
        "AND l.revision = c.revision WHERE c.player_id IS NOT NULL AND c.entity_confidence >= ? "
        "AND c.kind IN ('availability', 'training', 'return') AND c.effective_at <= ? "
        "AND c.expires_at > ? ORDER BY c.effective_at",
        [cutoff, MIN_ENTITY, cutoff, cutoff],
    )


def excluded_counts(db: FootballDB, cutoff: datetime) -> dict[str, int]:
    """Why claims did not count at this cutoff (for coverage reporting)."""
    q = lambda sql: db.con.execute(sql, [cutoff]).fetchone()[0]  # noqa: E731
    return {
        "post_cutoff": q("SELECT count(*) FROM evidence_claims WHERE effective_at > ?"),
        "expired": q("SELECT count(*) FROM evidence_claims WHERE expires_at <= ?"),
        "unresolved": q("SELECT count(*) FROM evidence_claims WHERE player_id IS NULL "
                        "AND effective_at <= ?"),  # fmt: skip
    }


def _weight(r) -> float:
    return TIER_W.get(int(r.source_tier), 0.4) * CERT_W.get(r.certainty, 0.4) * float(
        r.entity_confidence)  # fmt: skip


def player_evidence(db: FootballDB, cutoff: datetime | None) -> dict[int, PlayerEvidence]:
    if cutoff is None:
        return {}
    claims = eligible_claims(db, cutoff)
    out: dict[int, PlayerEvidence] = {}
    for pid, g in claims.groupby("player_id"):
        pid = int(pid)
        ev = PlayerEvidence(pid, "none")
        ret = g[g["kind"] == "return"]
        if len(ret):
            ev.return_at = pd.Timestamp(ret["return_at"].max()).to_pydatetime()
        sig = g[g["kind"] != "return"].copy()
        if sig.empty:
            if ev.return_at is not None:
                ev.state, ev.weight = "applied", float(max(_weight(r) for r in ret.itertuples()))
                ev.claims = [_cite(r) for r in ret.itertuples()]
            out[pid] = ev
            continue
        sig["signal"] = [SIGNAL.get((k, v)) for k, v in zip(sig["kind"], sig["value"], strict=True)]
        sig = sig.dropna(subset=["signal"])
        newest = sig.sort_values("effective_at").groupby("story_key").tail(1)  # one per story
        newest = newest.sort_values("effective_at", ascending=False)
        ev.stories = len(newest)
        top = newest.iloc[0]
        recent_confirmed = newest[(newest["certainty"] == "confirmed") & (
            newest["effective_at"] >= top["effective_at"] - CONFLICT_WINDOW)]  # fmt: skip
        if len(recent_confirmed) >= 2 and (recent_confirmed["signal"].max()
                                           - recent_confirmed["signal"].min()) >= 0.5:  # fmt: skip
            ev.state, ev.note = "conflict", "confirmed reports disagree"
            ev.claims = [_cite(r) for r in recent_confirmed.itertuples()]
            out[pid] = ev
            continue
        w = _weight(top)
        agree = newest.iloc[1:]
        agree = agree[(agree["signal"] - top["signal"]).abs() < 0.2]
        if len(agree):
            w = min(1.0, w + 0.5 * max(_weight(r) for r in agree.itertuples()))
        ev.state, ev.p_play, ev.weight = "applied", float(top["signal"]), float(w)
        ev.claims = [_cite(top), *(_cite(r) for r in agree.itertuples())]
        if ev.return_at is not None:
            ev.claims += [_cite(r) for r in ret.itertuples()]
        out[pid] = ev
    return out


def _cite(r) -> dict:
    return {"claim_id": r.claim_id, "source": r.source_id, "tier": int(r.source_tier),
            "kind": r.kind, "value": r.value, "certainty": r.certainty,
            "effective_at": str(r.effective_at), "quote": str(r.quote)[:200]}  # fmt: skip
