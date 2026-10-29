"""Bounded public news monitoring. Evidence is advisory, never an injury probability oracle.

Official City press conferences and BBC Football are currently covered. Unknown dates,
old stories, failures and uncovered clubs remain explicit. No article changes FPL flags.
"""

from __future__ import annotations

import json
import re
import uuid
from datetime import UTC, datetime, timedelta
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit
from xml.etree import ElementTree as ET

import httpx

CITY = "https://www.mancity.com/news/mens"
BBC = "https://feeds.bbci.co.uk/sport/football/rss.xml"
ALLOWED = {"www.mancity.com", "www.bbc.com", "www.bbc.co.uk", "feeds.bbci.co.uk"}
MAX_BYTES = 2_000_000


def timestamp(value):
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        try:
            dt = parsedate_to_datetime(str(value))
        except (ValueError, TypeError):
            return None
    return dt.replace(tzinfo=UTC) if dt.tzinfo is None else dt.astimezone(UTC)


def freshness(published, at):
    if published is None:
        return "undated"
    if published > at:
        return "future"
    return "fresh" if at - published <= timedelta(days=7) else "stale"


class Page(HTMLParser):
    def __init__(self):
        super().__init__()
        self.links = []
        self.published = None
        self.title = ""
        self.body = []
        self._title = False
        self._paragraph = False
        self._script = False
        self._json = ""

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "a" and a.get("href"):
            self.links.append(a["href"])
        if tag == "meta":
            key = a.get("property", a.get("name", "")).lower()
            if key in ("article:published_time", "datepublished"):
                self.published = timestamp(a.get("content")) or self.published
            if key == "og:title":
                self.title = a.get("content", "")
        if tag == "time" and a.get("datetime"):
            self.published = self.published or timestamp(a["datetime"])
        if tag == "script":
            self._script = a.get("type") == "application/ld+json"
            self._json = ""
        if tag == "h1":
            self._title = True
        if tag == "p":
            self._paragraph = True

    def handle_data(self, data):
        if self._script:
            self._json += data
        elif self._title:
            if not self.title:
                self.title = data.strip()
        elif self._paragraph:
            self.body.append(data.strip())

    def handle_endtag(self, tag):
        if tag == "h1":
            self._title = False
        if tag == "p":
            self._paragraph = False
        if tag == "script" and self._script:
            self._script = False
            try:
                self._structured(json.loads(self._json))
            except (ValueError, TypeError):
                pass

    def _structured(self, data):
        if isinstance(data, list):
            for row in data:
                self._structured(row)
        elif isinstance(data, dict):
            if "datePublished" in data:
                self.published = timestamp(data["datePublished"]) or self.published
            if "@graph" in data:
                self._structured(data["@graph"])


def _get(client, url):
    u = urlsplit(url)
    if u.scheme != "https" or u.hostname not in ALLOWED or u.port not in (None, 443):
        raise ValueError("source outside verified HTTPS news hosts")
    # Redirects are deliberately not followed: never turn a public URL into a local request.
    with client.stream("GET", url, follow_redirects=False) as response:
        response.raise_for_status()
        if response.is_redirect:
            raise ValueError("news redirect requires source review")
        chunks, size = [], 0
        for chunk in response.iter_bytes():
            size += len(chunk)
            if size > MAX_BYTES:
                raise ValueError("news response too large")
            chunks.append(chunk)
        return b"".join(chunks).decode("utf-8", errors="replace")


def monitor(db, names: list[str], *, urls=(), client=None, at=None):
    """At most eight articles plus two indexes per refresh; retain source failure evidence."""
    at = at or datetime.now(UTC)
    owned_client = client is None
    client = client or httpx.Client(timeout=8)
    observations = []

    def record(url, title="", body="", published=None, error=None):
        text = f"{title} {body}"
        # Full-name or exact surname matching only for distinct watched surnames.
        surnames = [n.split()[-1].casefold() for n in names if n.split()]
        matched = [
            n
            for n in names
            if n
            and (
                n.casefold() in text.casefold()
                or (
                    surnames.count(n.split()[-1].casefold()) == 1
                    and re.search(r"\b" + re.escape(n.split()[-1]) + r"\b", text, re.I)
                )
            )
        ]
        state = "failed" if error else freshness(published, at)
        row = {
            "url": url,
            "title": title[:200],
            "players": matched,
            "published_at": published.isoformat() if published else None,
            "observed_at": at.isoformat(),
            "state": state,
            "source": "official club"
            if urlsplit(url).hostname == "www.mancity.com"
            else "sports news",
            "error": error,
        }
        observations.append(row)
        db.con.execute(
            "INSERT INTO news_evidence VALUES (?, ?, ?, ?)",
            [uuid.uuid4().hex, at.replace(tzinfo=None), url, json.dumps(row)],
        )

    try:
        candidates = list(urls)[:8]
        try:
            page = Page()
            page.feed(_get(client, CITY))
            candidates += [
                urljoin(CITY, x)
                for x in page.links
                if "/news/mens/" in x
                and any(
                    w in x.lower() for w in ("injury", "press-conference", "team-news", "fitness")
                )
            ]
            record(CITY, "Official City index checked; article coverage bounded")
        except (httpx.HTTPError, ValueError) as exc:
            record(CITY, error=type(exc).__name__)
        try:
            root = ET.fromstring(_get(client, BBC))
            record(BBC, "BBC Football feed checked")
            for item in root.findall(".//item")[:40]:
                title = item.findtext("title", "")
                body = item.findtext("description", "")
                record(item.findtext("link", BBC), title, body, timestamp(item.findtext("pubDate")))
        except (httpx.HTTPError, ValueError, ET.ParseError) as exc:
            record(BBC, error=type(exc).__name__)
        for url in list(dict.fromkeys(candidates))[:8]:
            try:
                page = Page()
                page.feed(_get(client, url))
                record(url, page.title, " ".join(page.body), page.published)
            except (httpx.HTTPError, ValueError) as exc:
                record(url, error=type(exc).__name__)
    finally:
        if owned_client:
            client.close()
    return observations


def evidence_notes(db, names, at):
    """Only show observations known at decision time, with freshness recomputed now."""
    at = at.replace(tzinfo=UTC) if at.tzinfo is None else at.astimezone(UTC)
    rows = db.con.execute(
        "SELECT payload FROM news_evidence WHERE observed_at <= ? "
        "AND observed_at >= ? ORDER BY observed_at DESC LIMIT 500",
        [at.replace(tzinfo=None), (at - timedelta(days=7)).replace(tzinfo=None)],
    ).fetchall()
    notes, seen = [], set()
    for (payload,) in rows:
        r = json.loads(payload)
        if r["url"] in seen:
            continue
        seen.add(r["url"])
        players = set(r["players"]) & set(names)
        if players:
            state = freshness(timestamp(r["published_at"]), at)
            notes.append(
                f"news [{state}; {r['source']}]: {', '.join(sorted(players))}: "
                f"{r['title']} | {r['url']} | published {r['published_at']}; "
                "context only, not a verified probability or next-PL availability"
            )
    return notes[:12] or [
        "news: no matching dated external evidence stored for this squad; "
        "FPL flags retained. Run news-check; club coverage is currently City plus BBC Football."
    ]
