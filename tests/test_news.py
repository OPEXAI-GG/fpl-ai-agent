"""News and social ingestion: registry, RSS/Atom and X adapters, provenance, revisions,
duplicates and reposts, rate limits, missing credentials, malformed feeds, health, extraction,
identity ambiguity and idempotence."""

import json
from datetime import datetime, timedelta

import httpx
import pytest

from fpl_agent.db.database import FootballDB
from fpl_agent.errors import SourceFormatError
from fpl_agent.news import Item, extract, registry, rss, x
from fpl_agent.news.entities import Resolver
from fpl_agent.news.fetch import MissingCredentials
from fpl_agent.news.ingest import health, run_sources
from fpl_agent.news.registry import SourceConfig
from fpl_agent.news.store import store_items
from tests.helpers import FIX
from tests.news_helpers import squad_db

NEWS = FIX / "news"
T0 = datetime(2026, 11, 3, 12, 0)
RSS_SRC = SourceConfig("bbc", "rss", tier=2, url="https://feeds.example/football.xml",
                       min_interval_minutes=60)  # fmt: skip
X_SRC = SourceConfig("x-clubs", "x", tier=1, accounts=("Arsenal",), max_items=10, max_pages=2)


def rss_doc(items: list[tuple[str, str, str]]) -> str:
    body = "".join(
        f"<item><title>{t}</title><description>{d}</description><link>https://e.x/{g}?utm_source=r"
        f"</link><guid>{g}</guid><pubDate>Mon, 02 Nov 2026 09:00:00 GMT</pubDate></item>"
        for g, t, d in items
    )
    return (
        f'<?xml version="1.0"?><rss version="2.0"><channel><title>t</title>{body}</channel></rss>'
    )


def client(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


# ---- registry -------------------------------------------------------------------------------
def test_shipped_registry_is_valid_and_has_a_verified_feed():
    srcs = registry.load(registry.CONFIG)
    ids = {s.id: s for s in srcs}
    assert ids["bbc-football"].kind == "rss" and ids["bbc-football"].url.startswith("https://")
    assert ids["x-clubs"].kind == "x" and ids["x-clubs"].accounts == ()  # explicit opt-in only


@pytest.mark.parametrize("bad", [
    {"sources": [{"id": "a", "kind": "ftp"}]},
    {"sources": [{"id": "a", "kind": "rss", "url": "http://insecure"}]},
    {"sources": [{"id": "a", "kind": "rss", "url": "https://x"}, {"id": "a", "kind": "x"}]},
    {"sources": [{"id": "a", "kind": "x", "tier": 7}]},
    ["not", "a", "mapping"],
])  # fmt: skip
def test_bad_registry_entries_are_rejected(bad):
    with pytest.raises(registry.RegistryError):
        registry.parse(bad)


def test_missing_registry_file_means_no_sources(tmp_path):
    assert registry.load(tmp_path / "none.yaml") == []


# ---- RSS / Atom ------------------------------------------------------------------------------
def test_recorded_bbc_feed_parses_with_provenance():
    items = rss.parse_feed((NEWS / "bbc_premier_league.xml").read_bytes())
    assert len(items) == 10
    first = items[0]
    assert first.title.startswith("Jeers again") and first.published_at.year == 2026
    assert (
        first.canonical_url.startswith("https://www.bbc.co.uk/sport/")
        and "at_" not in first.canonical_url
    )


def test_atom_feeds_parse():
    doc = ('<feed xmlns="http://www.w3.org/2005/Atom"><title>t</title><entry><id>tag:1</id>'
           '<title>Saka back in training</title><link href="https://e.x/a"/>'
           '<updated>2026-11-02T08:00:00Z</updated><summary>Bukayo Saka is back in training.'
           '</summary><author><name>Reporter</name></author></entry></feed>')  # fmt: skip
    (it,) = rss.parse_feed(doc.encode())
    assert it.provider_id == "tag:1" and it.author == "Reporter"
    assert it.canonical_url == "https://e.x/a" and it.published_at == datetime(2026, 11, 2, 8)


@pytest.mark.parametrize("body", [
    b"", b"<html><body>Client Challenge</body></html>", (NEWS / "guardian_bot_check.html").read_bytes(),
    b"<rss><channel><item><title>cut", b'{"json": true}', b"<opml></opml>",
    b'<?xml version="1.0"?><!DOCTYPE r [<!ENTITY a "aaaa">]><rss></rss>',
])  # fmt: skip
def test_malformed_feeds_raise_format_errors(body):
    with pytest.raises(SourceFormatError):
        rss.parse_feed(body)


def test_markup_in_feed_text_is_stripped():
    (it,) = rss.parse_feed(
        rss_doc(
            [("g1", "Hi", "&lt;b&gt;Saka&lt;/b&gt; &lt;script&gt;x&lt;/script&gt; ok")]
        ).encode()
    )
    assert "<" not in it.content and "Saka" in it.content


# ---- storage: provenance, idempotence, revisions, duplicates -----------------------------------
def test_repeated_ingestion_is_idempotent_and_keeps_first_seen():
    db = squad_db()
    doc = rss_doc([("g1", "Saka ruled out", "Bukayo Saka has been ruled out of Saturday's game.")])
    http = client(lambda r: httpx.Response(200, text=doc))
    run_sources(db, [RSS_SRC], http, at=T0)
    run_sources(db, [RSS_SRC], http, at=T0 + timedelta(hours=2))
    run_sources(db, [RSS_SRC], http, at=T0 + timedelta(hours=4), force=True)
    assert db.con.execute("SELECT count(*) FROM news_items").fetchone()[0] == 1
    assert db.con.execute("SELECT count(*) FROM news_revisions").fetchone()[0] == 1
    assert db.con.execute("SELECT count(*) FROM evidence_claims").fetchone()[0] == 1
    first, pub = db.con.execute("SELECT first_seen_at, published_at FROM news_items").fetchone()
    assert first == T0 and pub == datetime(2026, 11, 2, 9)  # what we knew vs what it says


def test_min_interval_bounds_requests():
    db = squad_db()
    calls = []
    http = client(lambda r: (calls.append(1), httpx.Response(200, text=rss_doc([])))[1])
    run_sources(db, [RSS_SRC], http, at=T0)
    rep = run_sources(db, [RSS_SRC], http, at=T0 + timedelta(minutes=10))
    assert len(calls) == 1 and rep.outcomes[0].status == "skipped"


def test_edited_items_become_revisions_and_old_claims_are_kept():
    db = squad_db()
    v1 = rss_doc([("g1", "Saka update", "Bukayo Saka is a doubt for Saturday.")])
    v2 = rss_doc([("g1", "Saka update", "Bukayo Saka has been ruled out for Saturday.")])
    run_sources(db, [RSS_SRC], client(lambda r: httpx.Response(200, text=v1)), at=T0)
    run_sources(db, [RSS_SRC], client(lambda r: httpx.Response(200, text=v2)),
                at=T0 + timedelta(hours=3))  # fmt: skip
    revs = db.query("SELECT revision, observed_at FROM news_revisions ORDER BY revision")
    assert list(revs["revision"]) == [1, 2]
    claims = db.query("SELECT revision, value, effective_at FROM evidence_claims ORDER BY revision")
    assert list(zip(claims["revision"], claims["value"], strict=True)) == [
        (1, "doubtful"),
        (2, "out"),
    ]
    assert claims["effective_at"].iloc[1] == T0 + timedelta(hours=3)
    assert db.con.execute("SELECT revision FROM news_items").fetchone()[0] == 2


def test_copied_stories_share_a_story_key():
    db = squad_db()
    a = SourceConfig("a", "rss", url="https://a/x", tier=2)
    b = SourceConfig("b", "rss", url="https://b/x", tier=3)
    text = "Bukayo Saka has been ruled out of the north London derby with a hamstring injury"
    store_items(db, a, [Item("rss", "1", "Saka out", text, canonical_url="https://a/1")], T0)
    store_items(db, b, [Item("rss", "9", "Saka out!", text + " says boss",
                             canonical_url="https://b/9")], T0 + timedelta(hours=1))  # fmt: skip
    store_items(db, b, [Item("rss", "10", "Other", "Palmer signs a new deal at Chelsea today",
                             canonical_url="https://b/10")], T0)  # fmt: skip
    keys = db.query("SELECT provider_id, story_key FROM news_items ORDER BY provider_id")
    k = dict(zip(keys["provider_id"], keys["story_key"], strict=True))
    assert k["1"] == k["9"] != k["10"]


def test_the_same_item_twice_in_one_response_is_stored_once():
    db = squad_db()
    it = Item("rss", "1", "t", "c")
    res = store_items(db, RSS_SRC, [it, it], T0)
    assert res.new == 1


# ---- X adapter -----------------------------------------------------------------------------
def x_handler(pages: dict, seen: list):
    def handler(req: httpx.Request) -> httpx.Response:
        seen.append(req)
        assert req.headers["authorization"] == "Bearer tkn"
        if "/users/by/username/" in req.url.path:
            return httpx.Response(200, json={"data": {"id": "42", "username": "Arsenal"}})
        token = req.url.params.get("pagination_token") or "first"
        return httpx.Response(200, json=pages[token])

    return handler


POST = {"id": "100", "text": "Team news: William Saliba is back in training.",
        "created_at": "2026-11-02T10:00:00.000Z", "edit_history_tweet_ids": ["100"]}  # fmt: skip


def test_x_timeline_paginates_and_resumes_from_since_id(monkeypatch):
    monkeypatch.setenv("X_BEARER_TOKEN", "tkn")
    db = squad_db()
    pages = {"first": {"data": [POST], "meta": {"newest_id": "100", "next_token": "p2"}},
             "p2": {"data": [{**POST, "id": "90", "text": "Saka is fit",
                              "edit_history_tweet_ids": ["90"]}], "meta": {"oldest_id": "90"}}}  # fmt: skip
    seen: list = []
    rep = run_sources(db, [X_SRC], client(x_handler(pages, seen)), at=T0)
    assert rep.outcomes[0].new == 2 and rep.outcomes[0].requests == 3  # user lookup + 2 pages
    cur = json.loads(db.con.execute("SELECT cursor FROM source_state").fetchone()[0])
    assert cur["Arsenal"] == {"id": "42", "since_id": "100"}
    seen.clear()
    run_sources(db, [X_SRC], client(x_handler({"first": {"meta": {"result_count": 0}}}, seen)),
                at=T0 + timedelta(hours=5))  # fmt: skip
    assert len(seen) == 1 and seen[0].url.params["since_id"] == "100"  # no user lookup again
    url = db.con.execute(
        "SELECT canonical_url FROM news_items WHERE provider_id = '100'"
    ).fetchone()
    assert url[0] == "https://x.com/Arsenal/status/100"


def test_x_edits_are_revisions_of_the_original_and_reposts_are_the_same_story():
    db = squad_db()
    edited = {**POST, "id": "101", "text": "Team news: William Saliba is ruled out.",
              "edit_history_tweet_ids": ["100", "101"]}  # fmt: skip
    repost = {"id": "200", "text": "RT: Saliba is back in training",
              "referenced_tweets": [{"type": "retweeted", "id": "100"}]}  # fmt: skip
    store_items(db, X_SRC, [x.post_item(POST, "Arsenal")], T0)
    store_items(db, X_SRC, [x.post_item(edited, "Arsenal"), x.post_item(repost, "fan")],
                T0 + timedelta(hours=1))  # fmt: skip
    rows = db.query("SELECT provider_id, revision, story_key FROM news_items ORDER BY provider_id")
    assert list(rows["revision"]) == [2, 1]  # the edit is revision 2 of post 100
    assert rows["story_key"].iloc[0] == rows["story_key"].iloc[1]


def test_x_without_a_token_is_skipped_not_failed(monkeypatch):
    monkeypatch.delenv("X_BEARER_TOKEN", raising=False)
    db = squad_db()
    rep = run_sources(db, [X_SRC], client(lambda r: pytest.fail("no request without a token")),
                      at=T0)  # fmt: skip
    assert rep.outcomes[0].status == "skipped" and not rep.failed
    with pytest.raises(MissingCredentials):
        x.fetch(client(lambda r: httpx.Response(500)), X_SRC, {}, T0)
    assert "skipped" in health(db, [X_SRC], T0)["state"].iloc[0]


def test_rate_limit_waits_until_the_reset_time(monkeypatch):
    monkeypatch.setenv("X_BEARER_TOKEN", "tkn")
    db = squad_db()
    reset = int((T0 + timedelta(minutes=15)).timestamp()) + 0
    calls = []

    def limited(req):
        calls.append(1)
        return httpx.Response(429, headers={"x-rate-limit-reset": str(reset)})

    rep = run_sources(db, [X_SRC], client(limited), at=T0)
    assert rep.outcomes[0].status == "rate_limited"
    rep = run_sources(db, [X_SRC], client(limited), at=T0 + timedelta(minutes=5), force=True)
    assert rep.outcomes[0].status == "skipped" and len(calls) == 1  # still waiting
    h = health(db, [X_SRC], T0 + timedelta(minutes=5))
    assert h["state"].iloc[0] == "rate_limited x1"


def test_retry_after_seconds_are_honoured():
    db = squad_db()
    http = client(lambda r: httpx.Response(429, headers={"retry-after": "120"}))
    run_sources(db, [RSS_SRC], http, at=T0)
    nxt = db.con.execute("SELECT next_allowed_at FROM source_state").fetchone()[0]
    assert nxt == T0 + timedelta(seconds=120)


def test_conditional_get_and_not_modified():
    db = squad_db()
    seen = []

    def handler(req):
        seen.append(dict(req.headers))
        if req.headers.get("if-none-match") == '"v1"':
            return httpx.Response(304)
        return httpx.Response(200, text=rss_doc([("g1", "t", "c")]), headers={"etag": '"v1"'})

    run_sources(db, [RSS_SRC], client(handler), at=T0)
    rep = run_sources(db, [RSS_SRC], client(handler), at=T0 + timedelta(hours=2))
    assert rep.outcomes[0].status == "not_modified" and seen[1]["if-none-match"] == '"v1"'


def test_a_failing_optional_source_does_not_stop_the_others():
    db = squad_db()
    good = SourceConfig("good", "rss", url="https://good/x", tier=2)
    doc = rss_doc([("g1", "Saka ruled out", "Bukayo Saka has been ruled out.")])

    def handler(req):
        if req.url.host == "feeds.example":
            return httpx.Response(200, text="<html>Client Challenge</html>")
        return httpx.Response(200, text=doc)

    rep = run_sources(db, [RSS_SRC, good], client(handler), at=T0)
    assert [o.status for o in rep.outcomes] == ["failed", "ok"]
    h = health(db, [RSS_SRC, good], T0).set_index("source")
    assert h.loc["bbc", "state"] == "failed x1" and h.loc["good", "state"] == "healthy"
    assert "HTML" in h.loc["bbc", "last_error"]
    assert h.loc["good", "state"] == "healthy"
    later = health(db, [good], T0 + timedelta(days=1)).iloc[0]
    assert later["state"] == "stale"


def test_oversized_responses_are_refused():
    db = squad_db()
    small = SourceConfig("bbc", "rss", url="https://feeds.example/x", max_bytes=100)
    rep = run_sources(db, [small], client(lambda r: httpx.Response(200, text="x" * 1000)), at=T0)
    assert rep.outcomes[0].status == "failed" and "larger" in rep.outcomes[0].note


# ---- entities and claims ---------------------------------------------------------------------
def item(text, title="", at=T0, tier=2, rev=1, key="s1"):
    return {
        "item_id": "i1",
        "revision": rev,
        "title": title,
        "content": text,
        "observed_at": at,
        "source_id": "bbc",
        "source_tier": tier,
        "story_key": key,
        "published_at": None,
    }


@pytest.fixture(scope="module")
def resolver():
    return Resolver.from_db(squad_db())


def test_ambiguous_names_stay_unresolved(resolver):
    ms = {m.text: m for m in resolver.resolve("James is out injured.")}
    assert ms["james"].status == "ambiguous" and ms["james"].player_id is None
    assert sorted(ms["james"].candidates) == [20, 30]
    (c,) = extract.rule_claims(item("James is out injured."), resolver)
    assert c.player_id is None and c.entity_confidence == 0.0


def test_club_context_or_full_name_resolves(resolver):
    (m,) = resolver.resolve("Chelsea's James is out")
    assert m.player_id == 20 and m.confidence == 0.7
    (m,) = resolver.resolve("Reece James trained alone")
    assert m.player_id == 20 and m.confidence == 0.95


def test_confirmed_and_speculative_claims_are_separated(resolver):
    conf = extract.rule_claims(item("Bukayo Saka has been ruled out of Saturday's game."), resolver)
    spec = extract.rule_claims(
        item("Bukayo Saka could miss Saturday's game, reports suggest."), resolver
    )
    assert [(c.kind, c.value, c.certainty) for c in conf] == [("availability", "out", "confirmed")]
    assert [(c.kind, c.value, c.certainty) for c in spec] == [
        ("availability", "out", "speculative")
    ]


def test_recorded_bbc_quote_about_grealish_is_an_unavailability(resolver):
    items = rss.parse_feed((NEWS / "bbc_football.xml").read_bytes())
    it = next(i for i in items if "Grealish" in i.title)
    cs = extract.rule_claims(item(it.content, title=it.title), resolver)
    assert ("availability", "out", 40) in {(c.kind, c.value, c.player_id) for c in cs}


def test_negated_phrases_make_no_claim(resolver):
    assert extract.rule_claims(item("Saka has not been ruled out."), resolver) == []


def test_return_expectations_and_expiry(resolver):
    cs = {c.kind: c for c in extract.rule_claims(
        item("Erling Haaland is out for three weeks with an ankle problem."), resolver)}  # fmt: skip
    assert cs["return"].value == "weeks:3" and cs["return"].return_at == T0 + timedelta(weeks=3)
    assert cs["availability"].expires_at == T0 + timedelta(days=7)
    assert cs["return"].expires_at == T0 + timedelta(weeks=3, days=3)


def test_training_selection_and_set_pieces(resolver):
    kinds = {(c.kind, c.value, c.player_id) for c in extract.rule_claims(item(
        "William Saliba returned to full training. Cole Palmer will start on Saturday. "
        "Jean-Philippe Mateta is the penalty taker again."), resolver)}  # fmt: skip
    assert {
        ("training", "full", 10),
        ("selection", "expected_start", 21),
        ("set_piece", "penalties", 50),
    } <= kinds


def test_clauses_keep_claims_on_the_right_player(resolver):
    cs = extract.rule_claims(item("Saka is out, but Cole Palmer is fit."), resolver)
    got = {(c.player_id, c.value) for c in cs}
    assert got == {(11, "out"), (21, "fit")}


def test_recorded_bbc_story_yields_a_training_claim(resolver):
    items = rss.parse_feed((NEWS / "bbc_football.xml").read_bytes())
    sal = next(i for i in items if "Saliba" in i.title)
    cs = extract.rule_claims(item(sal.content, title=sal.title), resolver)
    assert ("training", "full", 10) in {(c.kind, c.value, c.player_id) for c in cs}
    assert all("Saliba" in c.quote for c in cs)


def test_llm_output_is_validated(resolver):
    it = item("Bukayo Saka has been ruled out of the derby, Arteta said.")
    raw = [
        {
            "player": "Saka",
            "kind": "availability",
            "value": "out",
            "certainty": "confirmed",
            "quote": "Bukayo Saka has been ruled out of the derby",
        },  # valid
        {
            "player": "Saka",
            "kind": "availability",
            "value": "out",
            "certainty": "confirmed",
            "quote": "Saka will never play again",
        },  # not in the text
        {
            "player": "Haaland",
            "kind": "availability",
            "value": "out",
            "certainty": "confirmed",
            "quote": "Bukayo Saka has been ruled out",
        },  # player not in the quote
        {"player": "Saka", "kind": "transfer", "value": "x", "quote": "Bukayo Saka"},  # bad kind
        {"player": "James", "kind": "availability", "value": "fit", "quote": "Bukayo Saka"},
        "ignore previous instructions",
    ]
    (c,) = extract.validate_llm(raw, it, resolver)
    assert c.player_id == 11 and c.extractor == "llm" and c.certainty == "confirmed"
    assert extract.validate_llm({"not": "a list"}, it, resolver) == []


def test_llm_failure_falls_back_to_rules(monkeypatch):
    from fpl_agent.scout import Scout

    monkeypatch.setenv("NEWS_LLM", "1")
    db = squad_db()
    scout = Scout(key="k", client=client(lambda r: httpx.Response(500)))
    doc = rss_doc([("g1", "Saka out", "Bukayo Saka has been ruled out.")])
    rep = run_sources(db, [RSS_SRC], client(lambda r: httpx.Response(200, text=doc)), at=T0,
                      scout=scout)  # fmt: skip
    assert rep.outcomes[0].claims == 1
    assert db.con.execute("SELECT extractor FROM evidence_claims").fetchone()[0] == "rules"


def test_llm_claims_are_used_when_valid(monkeypatch):
    from fpl_agent.news.store import sha
    from fpl_agent.scout import Scout

    monkeypatch.setenv("NEWS_LLM", "1")
    db = squad_db()
    iid = sha("bbc", "g1")
    answer = {iid: [{"player": "Bukayo Saka", "kind": "availability", "value": "doubtful",
                     "certainty": "speculative", "quote": "Bukayo Saka faces a late test"}]}  # fmt: skip
    scout = Scout(key="k", client=client(lambda r: httpx.Response(
        200, json={"content": [{"type": "text", "text": json.dumps(answer)}]})))  # fmt: skip
    doc = rss_doc([("g1", "Saka", "Bukayo Saka faces a late test on his knee.")])
    run_sources(db, [RSS_SRC], client(lambda r: httpx.Response(200, text=doc)), at=T0, scout=scout)
    row = db.con.execute("SELECT extractor, value, certainty FROM evidence_claims").fetchone()
    assert row == ("llm", "doubtful", "speculative")


def test_news_tables_survive_parquet_round_trip(tmp_path):
    db = squad_db()
    doc = rss_doc([("g1", "Saka ruled out", "Bukayo Saka has been ruled out.")])
    run_sources(db, [RSS_SRC], client(lambda r: httpx.Response(200, text=doc)), at=T0)
    db.export_parquet(tmp_path)
    fresh = FootballDB()
    fresh.import_parquet(tmp_path)
    fresh.import_parquet(tmp_path)  # restoring twice changes nothing
    for t in ("news_items", "news_revisions", "evidence_claims", "source_state"):
        assert fresh.counts()[t] == db.counts()[t] == 1
