"""Storing items with provenance.

* `item_id` = hash(source id, provider id): the same post fetched twice is one row (idempotent).
* `first_seen_at` is when this system first observed the item and never changes;
  `published_at` is the source's own timestamp and is never used as "when we knew".
* A changed title/body (an edited article or post) becomes a new row in `news_revisions` with its
  own `observed_at`; the item points at its newest revision. Nothing is overwritten.
* `story_key` groups copies: a repost/quote shares its original's key, an item with the same
  canonical URL or near-identical text (word-shingle Jaccard >= 0.6 within three days) joins the
  earlier story. Independent confirmation counts stories, not items.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import datetime, timedelta

import pandas as pd

from fpl_agent.data.id_map import normalise
from fpl_agent.db.database import FootballDB
from fpl_agent.news import Item
from fpl_agent.news.registry import SourceConfig

SIMILAR = 0.6
STORY_WINDOW = timedelta(days=3)


def sha(*parts: str) -> str:
    return hashlib.sha256("\x1f".join(parts).encode()).hexdigest()[:24]


def content_hash(title: str, content: str) -> str:
    return sha(re.sub(r"\s+", " ", title).strip(), re.sub(r"\s+", " ", content).strip())


def shingles(text: str, k: int = 4) -> set[str]:
    words = normalise(text).split()
    return (
        {" ".join(words[i : i + k]) for i in range(max(len(words) - k + 1, 1))} if words else set()
    )


def jaccard(a: set[str], b: set[str]) -> float:
    return len(a & b) / len(a | b) if a and b else 0.0


@dataclass
class StoreResult:
    new: int = 0
    revised: int = 0
    unchanged: int = 0
    changed_ids: list[tuple[str, int]] | None = None  # (item_id, revision) to extract from


def _story_key(db: FootballDB, src: SourceConfig, it: Item, item_id: str, at: datetime) -> str:
    if it.repost_of:  # a repost/quote is the original's story, even if we never saw it
        row = db.con.execute(
            "SELECT story_key FROM news_items WHERE provider = ? AND provider_id = ? LIMIT 1",
            [it.provider, it.repost_of],
        ).fetchone()
        return row[0] if row else sha(it.provider, it.repost_of)
    if it.canonical_url:
        row = db.con.execute(
            "SELECT story_key FROM news_items WHERE canonical_url = ? LIMIT 1", [it.canonical_url]
        ).fetchone()
        if row:
            return row[0]
    mine = shingles(f"{it.title} {it.content}")
    recent = db.con.execute(
        "SELECT story_key, title, content FROM news_items WHERE first_seen_at >= ? "
        "ORDER BY first_seen_at",
        [at - STORY_WINDOW],
    ).fetchall()
    for key, title, content in recent:
        if jaccard(mine, shingles(f"{title} {content}")) >= SIMILAR:
            return key
    return item_id


def store_items(db: FootballDB, src: SourceConfig, items: list[Item], at: datetime) -> StoreResult:
    """Insert new items, add a revision for changed ones, leave unchanged ones alone."""
    res = StoreResult(changed_ids=[])
    seen: set[str] = set()
    for it in items:
        item_id = sha(src.id, it.provider_id)
        if item_id in seen:  # the same item twice in one response
            continue
        seen.add(item_id)
        h = content_hash(it.title, it.content)
        row = db.con.execute(
            "SELECT content_hash, revision FROM news_items WHERE item_id = ?", [item_id]
        ).fetchone()
        if row and row[0] == h:
            db.con.execute(
                "UPDATE news_items SET retrieved_at = ? WHERE item_id = ?", [at, item_id]
            )
            res.unchanged += 1
            continue
        rev = 1 if row is None else int(row[1]) + 1
        db.append("news_revisions", pd.DataFrame([{
            "item_id": item_id, "revision": rev, "content_hash": h, "title": it.title,
            "content": it.content, "observed_at": at, "retrieved_at": at}]))  # fmt: skip
        if row is None:
            db.append("news_items", pd.DataFrame([{
                "item_id": item_id, "source_id": src.id, "provider": it.provider,
                "provider_id": it.provider_id, "canonical_url": it.canonical_url,
                "author": it.author, "published_at": it.published_at, "first_seen_at": at,
                "retrieved_at": at, "content_hash": h, "title": it.title, "content": it.content,
                "revision": rev, "story_key": _story_key(db, src, it, item_id, at),
                "repost_of": it.repost_of, "source_tier": src.tier,
                "raw": json.dumps(it.raw, default=str)}]))  # fmt: skip
            res.new += 1
        else:
            db.con.execute(
                "UPDATE news_items SET content_hash = ?, title = ?, content = ?, revision = ?, "
                "retrieved_at = ? WHERE item_id = ?",
                [h, it.title, it.content, rev, at, item_id],
            )
            res.revised += 1
        res.changed_ids.append((item_id, rev))
    return res
