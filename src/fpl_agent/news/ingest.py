"""Incremental news ingestion with source health.

For each enabled source in the registry:

1. Skip it when it ran successfully less than `min_interval_minutes` ago, or a rate limit told us
   to wait (`next_allowed_at`): bounded requests, and repeated runs do nothing new.
2. Fetch with its cursor (RSS: ETag/Last-Modified; X: per-account since_id), store items with
   provenance and revisions (`news.store`), then extract claims from every new or revised item.
3. Record the outcome in `source_state` (cursor, last success, consecutive failures) and
   `source_runs` (one row per attempt).

Every source is optional: an error is recorded and the next source runs. A missing credential
is "skipped", not a failure. Nothing here touches the projection tables, so a broken news
source can never stop the weekly plan.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from datetime import datetime, timedelta

import httpx
import pandas as pd

from fpl_agent.db.database import FootballDB, now
from fpl_agent.errors import SourceError
from fpl_agent.news import extract, rss, x
from fpl_agent.news.entities import Resolver
from fpl_agent.news.fetch import MissingCredentials, RateLimited
from fpl_agent.news.registry import SourceConfig
from fpl_agent.news.store import store_items

log = logging.getLogger(__name__)
ADAPTERS = {"rss": rss.fetch, "x": x.fetch}
STALE_AFTER = 3  # a source is "stale" once it has not succeeded for this many intervals


@dataclass
class SourceOutcome:
    source_id: str
    status: str  # ok | not_modified | skipped | rate_limited | failed | disabled
    requests: int = 0
    fetched: int = 0
    new: int = 0
    revised: int = 0
    unchanged: int = 0
    claims: int = 0
    note: str = ""


@dataclass
class IngestReport:
    outcomes: list[SourceOutcome] = field(default_factory=list)

    def summary(self) -> str:
        if not self.outcomes:
            return "news: no sources configured"
        return "\n".join(
            f"news {o.source_id}: {o.status} (requests {o.requests}, new {o.new}, revised "
            f"{o.revised}, unchanged {o.unchanged}, claims {o.claims})"
            + (f" - {o.note}" if o.note else "")
            for o in self.outcomes
        )

    @property
    def failed(self) -> list[SourceOutcome]:
        return [o for o in self.outcomes if o.status == "failed"]


def _state(db: FootballDB, sid: str) -> dict:
    df = db.query("SELECT * FROM source_state WHERE source_id = ?", [sid])
    return df.iloc[0].to_dict() if len(df) else {}


def _save_state(db: FootballDB, src: SourceConfig, prev: dict, **kw) -> None:
    row = {"source_id": src.id, "kind": src.kind, "cursor": prev.get("cursor"),
           "etag": prev.get("etag"), "last_modified": prev.get("last_modified"),
           "last_attempt_at": prev.get("last_attempt_at"),
           "last_success_at": prev.get("last_success_at"), "last_status": prev.get("last_status"),
           "last_error": prev.get("last_error"),
           "consecutive_failures": int(prev.get("consecutive_failures") or 0),
           "next_allowed_at": prev.get("next_allowed_at"),
           "items_total": int(prev.get("items_total") or 0)}  # fmt: skip
    row.update(kw)
    for k, v in list(row.items()):
        if isinstance(v, float) and v != v:  # NaN from an empty pandas cell
            row[k] = None
    db.upsert("source_state", pd.DataFrame([row]))


def _due(src: SourceConfig, st: dict, at: datetime, force: bool) -> str | None:
    nxt = st.get("next_allowed_at")
    if nxt is not None and not pd.isna(nxt) and pd.Timestamp(nxt) > pd.Timestamp(at):
        return f"rate limited until {pd.Timestamp(nxt):%Y-%m-%d %H:%M} UTC"
    if force:
        return None
    last = st.get("last_success_at")
    if last is not None and not pd.isna(last):
        if pd.Timestamp(at) - pd.Timestamp(last) < timedelta(minutes=src.min_interval_minutes):
            return f"fetched less than {src.min_interval_minutes} min ago"
    return None


def extract_items(db: FootballDB, changed: list[tuple[str, int]], resolver: Resolver,
                  scout=None) -> int:  # fmt: skip
    """Entities and claims for the given (item, revision) pairs; returns claims stored."""
    if not changed:
        return 0
    items = []
    for item_id, rev in changed:
        r = db.query(
            "SELECT i.item_id, v.revision, v.title, v.content, v.observed_at, i.source_id, "
            "i.source_tier, i.story_key, i.published_at FROM news_items i JOIN news_revisions v "
            "USING (item_id) WHERE i.item_id = ? AND v.revision = ?",
            [item_id, rev],
        )
        if len(r):
            d = r.iloc[0].to_dict()
            d["observed_at"] = pd.Timestamp(d["observed_at"]).to_pydatetime()
            pub = d.get("published_at")
            d["published_at"] = None if pub is None or pd.isna(pub) else pd.Timestamp(pub)
            items.append(d)
    llm: dict[str, list] = {}
    if scout is not None and os.environ.get("NEWS_LLM", "").strip() == "1":
        from fpl_agent.scout import claims_request

        llm = claims_request(scout, items)
    n = 0
    for it in items:
        claims = []
        if it["item_id"] in llm:
            claims = extract.validate_llm(llm[it["item_id"]], it, resolver)
        if not claims:
            claims = extract.rule_claims(it, resolver)
        ents = resolver.resolve(f"{it['title']}. {it['content']}")
        if ents:
            db.upsert("news_entities", pd.DataFrame([{
                "item_id": it["item_id"], "revision": int(it["revision"]), "mention": m.text,
                "player_id": m.player_id, "team_id": m.team_id, "status": m.status,
                "confidence": m.confidence, "candidates": json.dumps(m.candidates)}
                for m in ents]).astype({"player_id": "Int64", "team_id": "Int64"}))  # fmt: skip
        if claims:
            df = pd.DataFrame([{**c.__dict__, "created_at": now()} for c in claims])
            df["player_id"] = df["player_id"].astype("Int64")
            db.upsert("evidence_claims", df)
            n += len(claims)
    return n


def run_sources(
    db: FootballDB,
    sources: list[SourceConfig],
    http: httpx.Client | None = None,
    at: datetime | None = None,
    force: bool = False,
    scout=None,
    only: set[str] | None = None,
) -> IngestReport:
    http = http or httpx.Client(timeout=20, follow_redirects=True)
    at = at or now()
    report = IngestReport()
    resolver = None
    for src in sources:
        if only and src.id not in only:
            continue
        if not src.enabled:
            report.outcomes.append(SourceOutcome(src.id, "disabled"))
            continue
        st = _state(db, src.id)
        why = _due(src, st, at, force)
        if why:
            report.outcomes.append(SourceOutcome(src.id, "skipped", note=why))
            continue
        out = SourceOutcome(src.id, "ok")
        try:
            items, cur, out.requests = ADAPTERS[src.kind](http, src, st, at)
            res = store_items(db, src, items, at)
            out.fetched, out.new, out.revised, out.unchanged = (
                len(items), res.new, res.revised, res.unchanged)  # fmt: skip
            if res.changed_ids:
                resolver = resolver or Resolver.from_db(db)
                out.claims = extract_items(db, res.changed_ids, resolver, scout)
            out.status = "ok" if items or out.requests == 0 else "not_modified"
            _save_state(db, src, st, cursor=cur.get("cursor"), etag=cur.get("etag"),
                        last_modified=cur.get("last_modified"), last_attempt_at=at,
                        last_success_at=at, last_status=out.status, last_error=None,
                        consecutive_failures=0, next_allowed_at=None,
                        items_total=int(st.get("items_total") or 0) + res.new)  # fmt: skip
        except MissingCredentials as exc:
            out.status, out.note = "skipped", str(exc)
            _save_state(db, src, st, last_attempt_at=at, last_status="skipped",
                        last_error=str(exc))  # fmt: skip
        except RateLimited as exc:
            out.status, out.note = "rate_limited", str(exc)
            _save_state(
                db,
                src,
                st,
                last_attempt_at=at,
                last_status="rate_limited",
                last_error=str(exc),
                next_allowed_at=exc.retry_at,
                consecutive_failures=int(st.get("consecutive_failures") or 0) + 1,
            )
        except (SourceError, Exception) as exc:  # noqa: BLE001 - one source never stops the rest
            msg = f"{type(exc).__name__}: {exc}"[:300]
            out.status, out.note = "failed", msg
            _save_state(
                db,
                src,
                st,
                last_attempt_at=at,
                last_status="failed",
                last_error=msg,
                consecutive_failures=int(st.get("consecutive_failures") or 0) + 1,
            )
            log.warning("news source %s failed: %s", src.id, msg)
        db.append("source_runs", pd.DataFrame([{
            "source_id": src.id, "run_at": at, "status": out.status, "requests": out.requests,
            "fetched": out.fetched, "new_items": out.new, "revised": out.revised,
            "unchanged": out.unchanged, "note": out.note}]))  # fmt: skip
        report.outcomes.append(out)
    return report


def health(db: FootballDB, sources: list[SourceConfig], at: datetime | None = None) -> pd.DataFrame:
    """One row per configured source: last success, failures in a row, status, items, claims."""
    at = at or now()
    rows = []
    for src in sources:
        st = _state(db, src.id)
        last = st.get("last_success_at")
        has_last = last is not None and not pd.isna(last)
        age_h = (pd.Timestamp(at) - pd.Timestamp(last)).total_seconds() / 3600 if has_last else None
        stale = (not has_last) or age_h > STALE_AFTER * src.min_interval_minutes / 60
        n_claims = db.con.execute(
            "SELECT count(*) FROM evidence_claims WHERE source_id = ?", [src.id]
        ).fetchone()[0]
        if not src.enabled:
            state = "disabled"
        elif st.get("last_status") == "skipped":
            state = "skipped (" + str(st.get("last_error") or "") + ")"
        elif st.get("last_status") in ("failed", "rate_limited"):
            state = f"{st.get('last_status')} x{int(st.get('consecutive_failures') or 0)}"
        elif stale:
            state = "stale" if has_last else "never fetched"
        else:
            state = "healthy"
        rows.append({"source": src.id, "kind": src.kind, "tier": src.tier, "state": state,
                     "last_success": pd.Timestamp(last) if has_last else None,
                     "age_hours": round(age_h, 1) if age_h is not None else None,
                     "items": int(st.get("items_total") or 0), "claims": int(n_claims),
                     "last_error": st.get("last_error")})  # fmt: skip
    return pd.DataFrame(rows)
