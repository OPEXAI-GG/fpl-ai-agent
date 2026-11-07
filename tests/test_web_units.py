"""Website building blocks: team-ID parsing, the app store (sessions, jobs, caches, limits), the
FPL gateway's caching and failure handling, the sample gateway and read-only snapshots."""

import json
import time

import httpx
import pytest

from fpl_agent.db.database import FootballDB
from fpl_agent.errors import SourceFormatError, SourceUnavailable
from fpl_agent.web import provider
from fpl_agent.web.config import WebSettings
from fpl_agent.web.entry_ref import InvalidEntry, parse_entry
from fpl_agent.web.provider import (
    EntryNotFound,
    Gateway,
    ProviderUnavailable,
    RateLimited,
    SampleGateway,
)
from fpl_agent.web.store import Store

# ---- team ID and link parsing -----------------------------------------------------------------


@pytest.mark.parametrize(
    "text, entry, source",
    [
        ("8", 8, "id"),
        ("  1234567 ", 1234567, "id"),
        (1234567, 1234567, "id"),
        ("https://fantasy.premierleague.com/entry/1234567/event/10", 1234567, "url"),
        ("https://fantasy.premierleague.com/entry/1234567/history", 1234567, "url"),
        ("http://www.fantasy.premierleague.com/entry/42/", 42, "url"),
        ("fantasy.premierleague.com/entry/42/transfers", 42, "url"),
        ("https://fantasy.premierleague.com/team/42", 42, "url"),
        ("https://fantasy.premierleague.com/entry/42/event/3?x=1#top", 42, "url"),
    ],
)
def test_ids_and_supported_links_are_accepted(text, entry, source):
    ref = parse_entry(text)
    assert (ref.entry_id, ref.source) == (entry, source)


@pytest.mark.parametrize(
    "text, words",
    [
        ("", "Enter your FPL team ID"),
        (None, "Enter your FPL team ID"),
        ("abc", "doesn't look like"),
        ("0", "positive whole numbers"),
        ("123456789", "positive whole numbers"),
        ("x" * 201, "too long"),
        ("https://evil.example/entry/1/", "Only links to fantasy.premierleague.com"),
        ("https://fantasy.premierleague.com.evil.example/entry/1/", "Only links"),
        ("ftp://fantasy.premierleague.com/entry/1/", "Only links"),
        ("https://user:pw@fantasy.premierleague.com/entry/1/", "login or port"),
        ("https://fantasy.premierleague.com:8443/entry/1/", "login or port"),
        ("https://fantasy.premierleague.com/leagues/314/standings/c", "doesn't include a team ID"),
        ("https://fantasy.premierleague.com/entry/1/../../api", "doesn't include a team ID"),
        (True, "Enter your FPL team ID"),
    ],
)
def test_bad_ids_and_other_hosts_are_refused_with_a_reason(text, words):
    with pytest.raises(InvalidEntry, match=words):
        parse_entry(text)


# ---- the app store ------------------------------------------------------------------------------


@pytest.fixture
def store(tmp_path):
    return Store(tmp_path / "app.sqlite")


def test_store_migrations_are_idempotent(tmp_path):
    a = Store(tmp_path / "s.sqlite")
    v = a.migrate()
    b = Store(tmp_path / "s.sqlite")
    assert b.migrate() == v >= 1


def test_two_visitors_have_separate_sessions_and_preferences(store):
    a, b = store.new_session(), store.new_session()
    assert a != b
    store.update_session(a, entry_id=111, prefs={"strategy": "points"})
    store.update_session(b, entry_id=222, prefs={"strategy": "rank", "target_rank": 10_000})
    sa, sb = store.session(a), store.session(b)
    assert (sa["entry_id"], sa["prefs"]) == (111, {"strategy": "points"})
    assert (sb["entry_id"], sb["prefs"]["strategy"]) == (222, "rank")
    store.update_session(a, prefs={"timezone": "Asia/Dubai"})  # merged, entry kept
    assert store.session(a)["prefs"] == {"strategy": "points", "timezone": "Asia/Dubai"}
    assert store.session(a)["entry_id"] == 111
    store.delete_session(a)
    assert store.session(a) is None and store.session(b) is not None
    assert store.session(None) is None and store.update_session("nope", prefs={}) is None


def test_identical_work_is_deduplicated_while_active_and_requeued_after(store):
    j1, c1 = store.enqueue("plan", "plan-k", {"x": 1}, session_id="s1", entry_id=1)
    j2, c2 = store.enqueue("plan", "plan-k", {"x": 1}, session_id="s2", entry_id=1)
    assert c1 and not c2 and j1.id == j2.id
    assert store.queue_depth("plan") == 1 and store.active_jobs_for("s1") == 1
    assert "session_id" not in j1.public()
    got = store.claim("w1")
    assert got.id == j1.id and got.status == "running" and got.attempts == 1
    assert store.claim("w2") is None  # one claim per job
    store.progress(j1.id, "half", 0.5, {"stage": "preliminary"})
    assert store.job(j1.id).result == {"stage": "preliminary"}
    store.finish(j1.id, "done", {"stage": "final"})
    assert store.job(j1.id).progress == 1.0
    j3, c3 = store.enqueue("plan", "plan-k", {"x": 1})
    assert c3 and j3.id != j1.id  # finished work does not block a new request
    assert store.latest_job("plan-k").id == j3.id
    with pytest.raises(AssertionError):
        store.finish(j3.id, "weird")


def test_forecasts_are_claimed_before_plans(store):
    p, _ = store.enqueue("plan", "p", {})
    f, _ = store.enqueue("forecast", "f", {})
    assert store.claim("w").id == f.id
    assert store.claim("w").id == p.id


def test_requeue_and_stale_recovery(store):
    j, _ = store.enqueue("plan", "k", {})
    store.claim("w")
    store.requeue(j.id, "waiting for the shared forecast")
    again = store.job(j.id)
    assert again.status == "queued" and again.attempts == 0
    store.claim("w")
    store.con().execute("UPDATE jobs SET heartbeat = 0 WHERE id = ?", [j.id])
    assert store.recover_stale(60) == 1 and store.job(j.id).status == "queued"
    store.claim("w")
    store.claim("w")  # nothing more queued
    store.con().execute("UPDATE jobs SET heartbeat = 0, attempts = 2 WHERE id = ?", [j.id])
    assert store.recover_stale(60, max_attempts=2) == 1
    dead = store.job(j.id)
    assert dead.status == "failed" and "stopped responding" in dead.error


def test_results_cache_and_revisions(store):
    store.put_result("k", "plan", "preliminary", {"a": 1}, entry_id=5, job_id="j")
    store.put_result("k", "plan", "final", {"a": 2})
    assert store.result("k")["stage"] == "final" and store.result("k")["body"] == {"a": 2}
    assert store.result("missing") is None
    store.cache_put("x", {"v": 1}, 100.0)
    assert store.cache_get("x") == (100.0, {"v": 1}) and store.cache_get("y") is None
    store.add_revision("r1", "/a", {"cutoff": "c1"})
    store.add_revision("r2", "/b", {"cutoff": "c2"})
    assert store.current_revision()["id"] == "r2"
    store.put_forecast("fc-1", "r1", "/f1", {"gws": [1]})
    store.put_forecast("fc-2", "r2", "/f2", {"gws": [2]})
    assert store.latest_forecast("r1")["key"] == "fc-1"
    assert store.latest_forecast()["key"] == "fc-2"
    assert store.forecast("nope") is None


def test_rate_limit_windows_reset_and_old_windows_are_pruned(store, monkeypatch):
    t = [1000.0]
    monkeypatch.setattr("fpl_agent.web.store._now", lambda: t[0])
    assert all(store.hit("b", 3, 60) for _ in range(3))
    assert not store.hit("b", 3, 60)
    assert store.hit("other", 3, 60)  # buckets are independent
    t[0] += 61
    assert store.hit("b", 3, 60)
    rows = store.con().execute("SELECT count(*) FROM rate_limits WHERE bucket = 'b'").fetchone()
    assert rows[0] == 1


# ---- the FPL gateway ----------------------------------------------------------------------------


class Client:
    """A stand-in FPLClient: counts calls and fails on demand."""

    def __init__(self):
        self.n = 0
        self.fail: Exception | None = None

    def bootstrap_raw(self):
        self.n += 1
        if self.fail:
            raise self.fail
        return {"n": self.n}

    def entry_raw(self, entry):
        self.n += 1
        if entry == 404:
            raise SourceUnavailable("FPL entry: HTTP 404")
        if self.fail:
            raise self.fail
        return {"id": entry}


def test_gateway_caches_within_the_ttl_and_forced_refresh_bypasses_it(store):
    c = Client()
    g = Gateway(store, client=c)
    a = g.bootstrap()
    assert a.body == {"n": 1} and not a.stale and a.meta()["source"] == provider.SOURCE
    assert g.bootstrap().body == {"n": 1} and c.n == 1
    assert g.bootstrap(force=True).body == {"n": 2}


@pytest.mark.parametrize(
    "exc",
    [
        SourceUnavailable("timeout"),
        SourceFormatError("not json"),
        httpx.ConnectError("refused"),
    ],
)
def test_provider_failure_serves_the_labelled_stale_copy(store, exc):
    c = Client()
    g = Gateway(store, client=c)
    g.bootstrap()
    store.cache_put("bootstrap", {"n": 1}, time.time() - 10_000)  # past its TTL
    c.fail = exc
    got = g.bootstrap()
    assert got.stale and got.body == {"n": 1} and "did not respond" in got.error
    m = got.meta()
    assert m["stale"] is True and m["error"]


def test_provider_failure_without_a_copy_is_unavailable_not_success(store):
    c = Client()
    c.fail = SourceUnavailable("timeout")
    with pytest.raises(ProviderUnavailable, match="not responding"):
        Gateway(store, client=c).bootstrap()


def test_unknown_entry_is_not_found(store):
    with pytest.raises(EntryNotFound):
        Gateway(store, client=Client()).entry(404)


def test_outbound_budget_serves_cache_or_refuses(store):
    c = Client()
    g = Gateway(store, client=c, outbound_per_minute=1)
    g.bootstrap()
    store.cache_put("bootstrap", {"n": 1}, time.time() - 10_000)
    got = g.bootstrap()
    assert got.stale and "budget" in got.error and c.n == 1
    with pytest.raises(RateLimited):
        g.entry(7)


def test_forced_refresh_is_limited_per_entry_and_session(store):
    g = Gateway(store, client=Client())
    assert g.may_refresh(1, "s")
    assert not g.may_refresh(1, "s")  # same entry inside five minutes
    assert g.may_refresh(2, "s") and g.may_refresh(3, "s")
    assert not g.may_refresh(4, "s")  # three per session per window


def test_sample_gateway_reads_recorded_files_and_never_calls_out(tmp_path):
    for name, body in {
        "bootstrap": {"events": []},
        "fixtures": [],
        "entry": {"id": 0},
        "history": {"current": []},
        "picks": {"picks": []},
        "transfers": [],
    }.items():
        (tmp_path / f"{name}.json").write_text(json.dumps(body))
    (tmp_path / "captured_at.txt").write_text("123.0")
    g = SampleGateway(tmp_path)
    assert g.sample and g.bootstrap().body == {"events": []}
    assert g.entry(0).meta()["source"] == provider.SAMPLE_SOURCE
    assert g.picks(0, 5).fetched_at == 123.0
    assert g.may_refresh(0, "s") is False
    with pytest.raises(ProviderUnavailable):
        g.standings_page(1)


# ---- read-only snapshots ------------------------------------------------------------------------


def test_read_only_database_cannot_be_written(tmp_path):
    db = FootballDB(tmp_path / "f.duckdb")
    db.close()
    ro = FootballDB.read_only(tmp_path / "f.duckdb")
    try:
        assert ro.con.execute("SELECT count(*) FROM matches").fetchone()[0] == 0
        with pytest.raises(Exception, match="read-only"):
            ro.con.execute("CREATE TABLE x (a INT)")
    finally:
        ro.close()


def test_settings_from_env(monkeypatch, tmp_path):
    monkeypatch.setenv("WEB_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("WEB_WORKERS", "99")
    monkeypatch.setenv("WEB_SAMPLES", "not a number")
    monkeypatch.setenv("WEB_ALLOW_ADMIN", "TRUE")
    s = WebSettings.from_env()
    assert s.data_dir == tmp_path and s.workers == 4 and s.samples == 1000 and s.allow_admin
    assert s.store_path.name == "app.sqlite" and s.forecast_dir.name == "forecasts"
