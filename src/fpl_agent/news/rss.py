"""RSS 2.0 and Atom feeds -> items. Fetched text is data: only titles, summaries, links, authors,
ids and dates are kept; markup is stripped; anything that is not a feed raises
`SourceFormatError` (an HTML bot-check page, an empty body, a truncated document)."""

from __future__ import annotations

import html
import re
import xml.etree.ElementTree as ET
from datetime import datetime
from email.utils import parsedate_to_datetime
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import httpx

from fpl_agent.errors import SourceFormatError
from fpl_agent.news import Item
from fpl_agent.news.fetch import get
from fpl_agent.news.registry import SourceConfig
from fpl_agent.pit import utc

ATOM = "{http://www.w3.org/2005/Atom}"
DC = "{http://purl.org/dc/elements/1.1/}"
TRACKING = ("utm_", "at_", "ocid", "cmp")
MAX_TEXT = 4000


def clean_text(s: str | None) -> str:
    s = html.unescape(re.sub(r"<[^>]+>", " ", s or ""))
    return re.sub(r"\s+", " ", s).strip()[:MAX_TEXT]


def canonical_url(url: str | None) -> str | None:
    """Scheme+host lower-cased, tracking parameters and fragments dropped."""
    if not url:
        return None
    parts = urlsplit(url.strip())
    if parts.scheme not in ("http", "https"):
        return None
    q = [(k, v) for k, v in parse_qsl(parts.query) if not k.lower().startswith(TRACKING)]
    return urlunsplit(("https", parts.netloc.lower(), parts.path, urlencode(q), ""))


def _date(s: str | None) -> datetime | None:
    if not s:
        return None
    s = s.strip()
    try:
        return utc(parsedate_to_datetime(s))
    except (TypeError, ValueError, IndexError):
        pass
    try:
        return utc(s)
    except ValueError:
        return None


def parse_feed(body: bytes, max_items: int = 60) -> list[Item]:
    text = body.lstrip()
    if not text:
        raise SourceFormatError("feed body is empty")
    if text[:200].lower().startswith((b"<!doctype html", b"<html")):
        raise SourceFormatError("feed returned an HTML page (bot check or error page)")
    if b"<!ENTITY" in text[:4000]:
        raise SourceFormatError("feed declares entities; refused")
    try:
        root = ET.fromstring(text)
    except ET.ParseError as exc:
        raise SourceFormatError(f"feed is not well-formed XML: {exc}") from exc
    items: list[Item] = []
    if root.tag == "rss" or root.find("channel") is not None:
        for el in root.iter("item"):
            link = (el.findtext("link") or "").strip()
            guid = (el.findtext("guid") or "").strip() or link
            title = clean_text(el.findtext("title"))
            if not guid or not title:
                continue
            items.append(Item(
                "rss", guid, title, clean_text(el.findtext("description")),
                canonical_url=canonical_url(link), published_at=_date(el.findtext("pubDate")),
                author=clean_text(el.findtext(f"{DC}creator") or el.findtext("author")) or None,
            ))  # fmt: skip
    elif root.tag == f"{ATOM}feed":
        for el in root.iter(f"{ATOM}entry"):
            link_el = el.find(f"{ATOM}link[@rel='alternate']")
            if link_el is None:  # (an Element with no children is falsy: test for None)
                link_el = el.find(f"{ATOM}link")
            link = link_el.get("href") if link_el is not None else None
            eid = (el.findtext(f"{ATOM}id") or "").strip() or (link or "")
            title = clean_text(el.findtext(f"{ATOM}title"))
            if not eid or not title:
                continue
            body_text = el.findtext(f"{ATOM}summary") or el.findtext(f"{ATOM}content")
            author = el.find(f"{ATOM}author")
            items.append(Item(
                "rss", eid, title, clean_text(body_text), canonical_url=canonical_url(link),
                published_at=_date(el.findtext(f"{ATOM}published")
                                   or el.findtext(f"{ATOM}updated")),
                author=clean_text(author.findtext(f"{ATOM}name")) if author is not None else None,
            ))  # fmt: skip
    else:
        raise SourceFormatError(f"not an RSS or Atom feed (root <{root.tag}>)")
    return items[:max_items]


def fetch(
    http: httpx.Client, src: SourceConfig, state: dict, now: datetime
) -> tuple[list[Item], dict, int]:
    """(items, new cursor state, requests made). A 304 returns no items and keeps the cursor."""
    headers = {}
    if state.get("etag"):
        headers["If-None-Match"] = state["etag"]
    if state.get("last_modified"):
        headers["If-Modified-Since"] = state["last_modified"]
    resp = get(http, src.url or "", now, headers=headers, timeout=src.timeout_seconds,
               max_bytes=src.max_bytes)  # fmt: skip
    if resp.not_modified:
        return [], state, 1
    items = parse_feed(resp.body, src.max_items)
    cur = {"etag": resp.headers.get("etag") or state.get("etag"),
           "last_modified": resp.headers.get("last-modified") or state.get("last_modified"),
           "cursor": state.get("cursor")}  # fmt: skip
    return items, cur, 1
