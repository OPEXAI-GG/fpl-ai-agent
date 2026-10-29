from contextlib import closing
from datetime import UTC, datetime, timedelta

import httpx
import pytest

from fpl_agent.db.database import FootballDB
from fpl_agent.news import BBC, CITY, Page, _get, evidence_notes, freshness, monitor

AT = datetime(2026, 10, 20, 18, tzinfo=UTC)
URL = "https://www.mancity.com/news/mens/team-news-test"


def test_publication_date_is_not_modified_date_or_current_crawl():
    page = Page()
    page.feed(
        '<script type="application/ld+json">{"@type":"NewsArticle",'
        '"datePublished":"2025-10-20T12:00:00Z",'
        '"dateModified":"2026-10-20T12:00:00Z"}</script>'
    )
    assert freshness(page.published, AT) == "stale"
    assert freshness(None, AT) == "undated"
    assert freshness(AT + timedelta(hours=1), AT) == "future"


def test_monitor_records_provenance_failure_and_does_not_clear_flags():
    def handler(request):
        url = str(request.url)
        if url == CITY:
            return httpx.Response(200, text=f'<a href="{URL}">Team news</a>')
        if url == BBC:
            return httpx.Response(503)
        return httpx.Response(
            200,
            text='<meta property="article:published_time" '
            'content="2026-10-19T12:00:00Z"><h1>City fitness update</h1>'
            "<p>Erling Haaland may feature against AEK.</p>",
        )

    with (
        closing(FootballDB()) as db,
        httpx.Client(transport=httpx.MockTransport(handler)) as client,
    ):
        rows = monitor(db, ["Erling Haaland"], client=client, at=AT)
        article = next(r for r in rows if r["url"] == URL)
        assert article["players"] == ["Erling Haaland"] and article["state"] == "fresh"
        assert any(r["state"] == "failed" for r in rows)
        notes = evidence_notes(db, ["Erling Haaland"], AT)
        assert URL in notes[0] and "not a verified probability" in notes[0]
        assert "no matching" in evidence_notes(db, ["Erling Haaland"], AT - timedelta(days=1))[0]
        assert "stale" in evidence_notes(db, ["Erling Haaland"], AT + timedelta(days=6))[0]


@pytest.mark.parametrize(
    "url",
    [
        "http://www.mancity.com/a",
        "https://localhost/a",
        "https://www.mancity.com.evil.org/a",
        "https://www.mancity.com:8443/a",
    ],
)
def test_news_cannot_fetch_arbitrary_or_local_hosts(url):
    with httpx.Client(transport=httpx.MockTransport(lambda _: pytest.fail("network reached"))) as c:
        with pytest.raises(ValueError):
            _get(c, url)


def test_news_rejects_redirects():
    with httpx.Client(
        transport=httpx.MockTransport(
            lambda _: httpx.Response(302, headers={"location": "http://127.0.0.1/secret"})
        )
    ) as c:
        with pytest.raises((ValueError, httpx.HTTPStatusError)):
            _get(c, URL)
