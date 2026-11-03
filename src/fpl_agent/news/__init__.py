"""News and social sources: registry, adapters, storage with provenance, and claim extraction."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class Item:
    """One post or article as a source returned it. `published_at` is the source's own claim of
    when it was published; when *we* first saw it is recorded separately at storage time."""

    provider: str  # "rss" | "x"
    provider_id: str  # stable id at the source (RSS guid/link, X original post id)
    title: str
    content: str
    canonical_url: str | None = None
    author: str | None = None
    published_at: datetime | None = None
    repost_of: str | None = None  # provider id of the original for reposts/quotes
    raw: dict = field(default_factory=dict)
