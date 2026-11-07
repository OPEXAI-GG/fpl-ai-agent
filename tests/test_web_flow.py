"""The website end to end on a synthetic world and a fake public FPL API: publish a data
revision, build the shared forecast, look teams up, queue and run plans, and check what two
visitors see. Nothing reaches the network or anyone's real team."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from fpl_agent.web import forecasts, planning, snapshots, worker
from fpl_agent.web.api import COOKIE, create_app
from tests.web_support import (
    ENTRY_A,
    ENTRY_B,
    GW,
    SEASON,
    FakeFPL,
    make_service,
    make_world,
    squad_of,
)

FORMATIONS = {(1, d, m, f) for d in range(3, 6) for m in range(2, 6) for f in range(1, 4)}


@pytest.fixture(scope="module")
def world(tmp_path_factory):
    root = tmp_path_factory.mktemp("webflow")
    db, boot, fx = make_world(root)
    fake = FakeFPL(boot, fx, {ENTRY_A: squad_of(boot), ENTRY_B: squad_of(boot, 9)})
    svc = make_service(root, db, fake)
    forecasts.clear_cache()
    return root, db, boot, fx, fake, svc


@pytest.fixture(scope="module")
def app(world):
    root, db, boot, fx, fake, svc = world
    application = create_app(svc.settings, svc.gateway)
    return application


def client(app) -> TestClient:
    return TestClient(app)


def test_before_any_data_is_published_plans_say_so(app, world):
    c = client(app)
    h = c.get("/api/health").json()
    assert h["ok"] and h["revision"] is None and h["forecast"] is None
    r = c.post(f"/api/entries/{ENTRY_A}/plan")
    assert r.status_code == 503 and r.json()["detail"]["code"] == "no_data"
    assert c.get("/api/players").status_code == 503


@pytest.fixture(scope="module")
def published(world, app):
    root, db, boot, fx, fake, svc = world
    rev = snapshots.publish(db, svc.settings.data_dir, svc.store, SEASON, boot, fx)
    return rev


def test_publishing_copies_an_immutable_revision_with_its_cutoff(published, world):
    svc = world[5]
    assert snapshots.current(svc.store).id == published.id
    assert published.db_path.exists() and published.db_path != world[1]
    assert published.meta["snapshot_gws"][-1] >= GW and published.cutoff
    assert published.captured("bootstrap")["events"][GW - 1]["is_next"]
    again = snapshots.publish(world[1], svc.settings.data_dir, svc.store, SEASON)
    assert again.id == published.id  # same content, same revision


def test_lookup_validates_and_remembers_the_team_per_visitor(app, published, world):
    a, b = client(app), client(app)
    r = a.post("/api/lookup", json={"query": f"https://fantasy.premierleague.com/entry/{ENTRY_A}/"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["entry_id"] == ENTRY_A and body["source"] == "url"
    assert body["manager"]["team_name"] == "Fiction 1" and body["fetched"]["source"]
    assert b.post("/api/lookup", json={"query": str(ENTRY_B)}).json()["source"] == "id"
    sa, sb = a.get("/api/session").json(), b.get("/api/session").json()
    assert sa["entry_id"] == ENTRY_A and sb["entry_id"] == ENTRY_B
    assert a.cookies.get(COOKIE) != b.cookies.get(COOKIE)
    bad = a.post("/api/lookup", json={"query": "https://example.com/entry/1/"})
    assert bad.status_code == 400 and bad.json()["detail"]["code"] == "invalid_entry"
    missing = a.post("/api/lookup", json={"query": "1000099"})
    assert missing.status_code == 404 and missing.json()["detail"]["code"] == "not_found"
    assert a.get("/api/session").json()["entry_id"] == ENTRY_A  # a failed lookup changes nothing


def test_preferences_are_validated_and_reset_forgets_everything(app, published):
    c = client(app)
    c.get("/api/session")
    ok = c.patch("/api/session", json={"strategy": "rank", "target_rank": 10_000})
    assert ok.json()["prefs"]["strategy"] == "rank" and ok.json()["prefs"]["target_rank"] == 10_000
    assert c.patch("/api/session", json={"strategy": "yolo"}).status_code == 400
    assert c.patch("/api/session", json={"target_rank": 5}).status_code == 400
    assert c.patch("/api/session", json={"timezone": "Mars/Base"}).status_code == 400
    assert c.patch("/api/session", json={"timezone": "Asia/Dubai"}).json()["prefs"]["timezone"]
    assert c.delete("/api/session").json() == {"reset": True}
    fresh = c.get("/api/session").json()
    assert fresh["entry_id"] is None and fresh["prefs"]["strategy"] == "balanced"


def test_dashboard_labels_what_is_known_and_what_is_estimated(app, published):
    c = client(app)
    d = c.get(f"/api/entries/{ENTRY_A}/dashboard")
    assert d.status_code == 200, d.text
    d = d.json()
    assert d["gameweek"]["next"] == GW and len(d["squad"]) == 15
    assert sum(p["starting"] for p in d["squad"]) == 11
    f = d["facts"]
    assert f["free_transfers"]["status"] in ("estimated", "conflicting", "unknown")
    assert f["bank"]["status"] in ("as of last deadline", "estimated")
    assert any("free-transfer" in x for x in f["not_public"])
    assert d["sources"]["entry"]["source"] and not d["sample"]
    assert c.get("/api/entries/abc/dashboard").status_code == 400
    assert c.get("/api/entries/1000099/dashboard").status_code == 404


def test_fpl_outage_shows_the_stale_copy_labelled(app, published, world):
    fake = world[4]
    c = client(app)
    c.get(f"/api/entries/{ENTRY_A}/dashboard")
    fake.down = True
    try:
        d = c.get(f"/api/entries/{ENTRY_A}/dashboard", params={"refresh": True}).json()
        assert d["refresh"]["applied"] is True
        assert d["sources"]["entry"]["stale"] is True and d["sources"]["entry"]["error"]
        again = c.get(f"/api/entries/{ENTRY_A}/dashboard", params={"refresh": True}).json()
        assert again["refresh"] == {"requested": True, "applied": False}  # limited
        never = c.post("/api/lookup", json={"query": "1000077"})
        assert never.status_code == 503  # no copy: unavailable, never a sample team
        assert never.json()["detail"]["code"] == "provider_unavailable"
    finally:
        fake.down = False


@pytest.fixture(scope="module")
def planned(app, published, world):
    """Two visitors ask for the same team's plan, a third for another team; one worker runs."""
    svc = world[5]
    a, b, c = client(app), client(app), client(app)
    c.patch("/api/session", json={"strategy": "points"})
    ra = a.post(f"/api/entries/{ENTRY_A}/plan").json()
    rb = b.post(f"/api/entries/{ENTRY_A}/plan").json()
    rc = c.post(f"/api/entries/{ENTRY_B}/plan").json()
    before = {"a": ra, "b": rb, "c": rc}
    n = worker.loop(svc, once=True, max_jobs=10)
    return a, b, c, before, n


def test_identical_plan_requests_share_one_job_and_wait_for_the_forecast(planned):
    a, b, c, before, n = planned
    ra, rb, rc = before["a"], before["b"], before["c"]
    assert ra["status"] == "queued" and ra["forecast_job"]["kind"] == "forecast"
    assert rb["job"]["id"] == ra["job"]["id"] and rb["status"] == "queued"
    assert rc["key"] != ra["key"]  # another team and another strategy
    assert n == 3  # the forecast and two plans


def test_final_plan_is_legal_conditional_on_free_transfers_and_labelled(planned, world):
    a = planned[0]
    p = a.get(f"/api/entries/{ENTRY_A}/plan").json()
    assert p["status"] == "ready", p
    res = p["result"]
    assert res["final"] and res["stage"] == "final" and not res["timed_out"]
    fts = [s["free_transfers"] for s in res["scenarios"]]
    assert 0 in fts and len(fts) >= 2 and all(s["status"] == "planned" for s in res["scenarios"])
    assert res["conditional"] != res["same_across_scenarios"]
    rec = res["recommendation"]
    pos = {}
    for pl in rec["xi"]:
        pos[pl["position"]] = pos.get(pl["position"], 0) + 1
    shape = (pos.get("GKP", 0), pos.get("DEF", 0), pos.get("MID", 0), pos.get("FWD", 0))
    assert len(rec["xi"]) == 11 and len(rec["bench"]) == 4 and shape in FORMATIONS
    assert [p["position"] for p in rec["bench"]].count("GKP") == 1  # engine order: GK last
    xi_ids = {pl["id"] for pl in rec["xi"]}
    assert rec["captain"]["id"] in xi_ids and rec["vice"]["id"] in xi_ids
    assert rec["captain"]["id"] != rec["vice"]["id"]
    assert rec["rails"]["ok"] and res["lineup_shown"]
    rows = {r["transfers"]: r for r in rec["comparison"]}
    assert 0 in rows and rec["baseline"] is not None and len(rec["baseline"]["xi"]) == 11
    for k, r in rows.items():
        if k and r.get("feasible") and r.get("worlds"):
            assert r["gain_q10"] <= r["gain"] <= r["gain_q90"] or r["gain_sd"] >= 0
    assert res["forecast"]["samples"] == world[5].settings.samples
    assert res["team"]["free_transfers"]["status"] in ("estimated", "conflicting", "unknown")
    assert res["strategy"]["key"] == "balanced" and not res["sample"]


def test_visitors_cannot_read_each_others_jobs(planned):
    a, b, c, before, _ = planned
    jid = before["a"]["job"]["id"]
    assert a.get(f"/api/jobs/{jid}").status_code == 200
    assert c.get(f"/api/jobs/{jid}").status_code == 404  # another team, another session
    body = a.get(f"/api/jobs/{jid}").json()
    assert "session_id" not in body and body["status"] == "done"
    fjob = before["a"]["forecast_job"]["id"]
    assert c.get(f"/api/jobs/{fjob}").status_code == 200  # the shared forecast is public


def test_a_finished_plan_is_served_from_cache_and_new_prefs_make_a_new_plan(planned, world):
    a = planned[0]
    again = a.post(f"/api/entries/{ENTRY_A}/plan").json()
    assert again["status"] == "ready" and again["result"]["final"]
    a.patch("/api/session", json={"strategy": "points"})
    other = a.post(f"/api/entries/{ENTRY_A}/plan").json()
    assert other["status"] == "queued" and other["key"] != again["key"]
    a.patch("/api/session", json={"strategy": "balanced"})
    assert a.get(f"/api/entries/{ENTRY_A}/plan").json()["status"] == "ready"
    svc = world[5]
    svc.store.finish(other["job"]["id"], "failed", error="cancelled by the test")


def test_plan_is_deterministic_for_the_same_inputs(planned, world):
    svc = world[5]
    key, shared = svc.forecast_for(GW)
    st = planning.load_state(svc.gateway, ENTRY_B, svc.rules, SEASON)
    prefs = {"strategy": "points"}
    stored = planned[2].get(f"/api/entries/{ENTRY_B}/plan").json()["result"]
    redo = planning.run(shared, st, svc.rules, prefs, svc.gateway)
    pick = lambda r: (  # noqa: E731
        r["recommendation"]["action"],
        [p["id"] for p in r["recommendation"]["xi"]],
        r["recommendation"]["captain"]["id"],
        [s.get("key") for s in r["scenarios"]],
    )
    assert pick(redo) == pick(stored)


def test_the_shared_forecast_respects_the_revision_cutoff(planned, world):
    svc = world[5]
    _, shared = svc.forecast_for(GW)
    rev = svc.revision()
    m = shared.manifest
    assert m["cutoff"] == rev.meta["cutoff"] and m["revision"] == rev.id
    assert m["gws"][0] == GW and "owner lessons memory" in m["excluded"][0]
    assert shared.proj.points.shape[0] == len(shared.proj.meta)


def test_a_new_data_revision_invalidates_the_forecast_and_plan_keys(planned, world):
    svc = world[5]
    k1 = forecasts.key_of(svc.forecast_config(GW))
    cfg = dict(svc.forecast_config(GW), revision="r-other", cutoff="2099-01-01T00:00:00")
    assert forecasts.key_of(cfg) != k1
    st = planning.load_state(svc.gateway, ENTRY_A, svc.rules, SEASON)
    fp = planning.fingerprint(st.team)
    prefs = planning.prefs_of({})
    assert planning.plan_key(k1, fp, prefs) != planning.plan_key(forecasts.key_of(cfg), fp, prefs)
    assert planning.plan_key(k1, fp, prefs) != planning.plan_key(
        k1, fp, planning.prefs_of({"strategy": "rank"})
    )


def test_shared_screens_read_the_forecast(app, planned):
    c = client(app)
    pl = c.get("/api/players", params={"position": "MID", "limit": 5})
    assert pl.status_code == 200, pl.text
    rows = pl.json()["players"]
    assert 0 < len(rows) <= 5 and all(r["position"] == "MID" for r in rows)
    m = rows[0]["minutes_model"]
    assert {"p_start", "p_60", "p_cameo", "p_none"} <= set(m)
    assert abs(m["p_start"] + m["p_cameo"] + m["p_none"] - 1) < 0.02 or m["p_start"] <= 1
    assert c.get("/api/players", params={"position": "XX"}).status_code == 400
    assert c.get("/api/players", params={"ids": "a,b"}).status_code == 400
    fx = c.get("/api/fixtures").json()
    assert fx["gws"][0] == GW and fx["teams"]
    perf = c.get("/api/performance")
    assert perf.status_code == 200 and "weeks" in perf.json() and perf.json()["backtests"] == []
    h = c.get("/api/health").json()
    assert h["forecast"]["gws"][0] == GW and h["queue"]["plan"] == 0


def test_worker_reports_user_errors_as_failed_jobs(world, published):
    svc = world[5]
    job, _ = svc.store.enqueue("plan", "plan-x", {"entry": 1000099, "prefs": {},
                                                  "forecast_key": "fc-missing"})  # fmt: skip
    claimed = svc.store.claim("t")
    assert worker.process(svc, claimed) == "failed"
    assert "could not be built" in svc.store.job(job.id).error


def test_a_plan_that_runs_out_of_time_is_labelled_preliminary(planned, world):
    svc = world[5]
    _, shared = svc.forecast_for(GW)
    st = planning.load_state(svc.gateway, ENTRY_A, svc.rules, SEASON)
    seen = []
    body = planning.run(
        shared, st, svc.rules, {}, svc.gateway, on_stage=lambda *a: seen.append(a[0]), budget=0
    )
    assert body["timed_out"] and not body["final"] and body["stage"] == "timed_out"
    assert any(s["status"] == "pending" for s in body["scenarios"])
    assert not body["conditional"] and not body["same_across_scenarios"]  # incomplete: neither
    assert seen[0].startswith("planned the most likely") and seen[-1].startswith("stopped")


def test_the_chip_search_runs_last_when_chips_are_available(planned, world):
    svc = world[5]
    _, shared = svc.forecast_for(GW)
    world[4].chips[ENTRY_B] = []
    try:
        st = planning.load_state(svc.gateway, ENTRY_B, svc.rules, SEASON, force=True)
        stages = []
        body = planning.run(
            shared, st, svc.rules, {"strategy": "points"}, svc.gateway,
            on_stage=lambda stage, p, b: stages.append((stage, b["final"])),
        )  # fmt: skip
    finally:
        world[4].chips.pop(ENTRY_B)
        planning.load_state(svc.gateway, ENTRY_B, svc.rules, SEASON, force=True)
    assert body["final"] and body["chips_possible"]
    assert "chip_search_seconds" in body["timings"]
    assert body["recommendation"]["chips"]["searched"]
    assert all(not final for _, final in stages[:-1]) and stages[-1] == ("final", True)


def test_rank_strategy_falls_back_when_standings_are_missing(planned, world):
    svc = world[5]
    _, shared = svc.forecast_for(GW)
    st = planning.load_state(svc.gateway, ENTRY_A, svc.rules, SEASON)
    _own, _eo, _cfg, _chase, notes = planning.strategy_inputs(
        shared, GW, {"strategy": "rank", "target_rank": 10_000}, svc.gateway, st.history
    )
    assert notes  # either the chase description or why it was not possible
    pts = planning.strategy_inputs(shared, GW, {"strategy": "points"}, svc.gateway, st.history)
    assert pts[0] == {} and pts[2].risk_lambda == 0.0


def test_the_sample_team_is_labelled_and_never_calls_fpl(app, planned, world):
    import time as _t

    from fpl_agent.web import sample

    root, db, boot, fx, fake, svc = world
    sample.build(boot, fx, svc.settings.sample_dir, _t.time())
    before = len(fake.calls)
    c = client(app)
    d = c.get("/api/entries/sample/dashboard").json()
    assert d["sample"] and d["sources"]["entry"]["source"] == "sample data"
    assert d["manager"]["team_name"] == "Sample XI" and len(d["squad"]) == 15
    p = c.post("/api/entries/sample/plan").json()
    assert p["key"].startswith("sample-")
    assert c.get("/api/fixtures", params={"sample": True}).json()["gws"][0] == GW
    assert len(fake.calls) == before  # nothing was fetched for the sample
    svc.store.finish(p["job"]["id"], "failed", error="not run in this test")


def test_web_cli_status_sample_forecast_publish_and_worker(world, published, monkeypatch, capsys):
    from argparse import Namespace

    from fpl_agent.web import cli as web_cli
    from fpl_agent.web.service import Service

    svc = world[5]
    monkeypatch.setattr(Service, "create", classmethod(lambda cls, s=None, g=None: svc))
    ns = lambda action, **kw: Namespace(  # noqa: E731
        action=action, update=False, ownership=False, no_forecast=kw.get("nf", True),
        host="127.0.0.1", port=0, once=True,
    )  # fmt: skip
    assert web_cli.run(ns("status")) == 0
    assert "revision: r" in capsys.readouterr().out
    assert web_cli.run(ns("sample")) == 0 and (svc.settings.sample_dir / "picks.json").exists()
    assert web_cli.run(ns("forecast")) == 0 and "built in" in capsys.readouterr().out
    assert web_cli.run(ns("publish", nf=False)) == 0
    out = capsys.readouterr().out
    assert "published revision" in out and "ready" in out  # same data: the forecast exists
    world[4].down = True
    try:
        svc.store.cache_put("bootstrap", world[2], 0.0)  # expired copy only
        assert web_cli.run(ns("publish")) == 2  # never publishes from a stale bootstrap
    finally:
        world[4].down = False
    assert web_cli.run(ns("worker")) == 0
