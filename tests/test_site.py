"""The website sync on the synthetic world: one shared projection, then a free pick or a full
plan per manager, posted to a fake site API."""

from datetime import UTC, datetime, timedelta

import pytest

from fpl_agent import cli, site
from fpl_agent.site import SiteClient, SyncOptions, sync
from tests.test_agent import FakeFPL, env  # noqa: F401  (the module fixture)

GW, SEASON = 10, 2024


@pytest.fixture(autouse=True)
def no_keys(monkeypatch):
    for k in ("ANTHROPIC_API_KEY", "ODDS_API_KEY", "SITE_URL", "ENGINE_TOKEN"):
        monkeypatch.delenv(k, raising=False)


class FakeSite:
    def __init__(self, managers):
        self._managers, self.posts = managers, []

    def managers(self, gw):
        return self._managers

    def post(self, path, body):
        self.posts.append((path, body))

    def plans(self):
        return [p for path, b in self.posts if path == "/api/engine/plans" for p in b["plans"]]


class BadFPL(FakeFPL):
    def entry_picks_raw(self, entry, gw):
        if entry == 404:
            raise RuntimeError("no such team")
        return super().entry_picks_raw(entry, gw)


def fpl(env, hours=30.0, cls=BadFPL):  # noqa: F811
    return cls(env[1], env[2], datetime.now(UTC) + timedelta(hours=hours))


def opts(**kw):
    return SyncOptions(season=SEASON, horizon=3, samples=150, **kw)


def test_free_full_and_failed_managers_each_get_a_plan(env):  # noqa: F811
    s = FakeSite([{"manager_id": 1, "tier": "free"}, {"manager_id": 2, "tier": "pro"},
                  {"manager_id": 404, "tier": "free"}])  # fmt: skip
    res = sync(env[0].db, s, opts(), fpl=fpl(env))
    assert res.status == "ok" and (res.free, res.full, res.failed) == (1, 1, 1), res.notes
    gw = [b for p, b in s.posts if p == "/api/engine/gameweek"][0]
    assert gw["gw"] == GW and 0 < len(gw["captains"]) <= site.TOP_CAPTAINS
    assert gw["captains"][0]["xp"] >= gw["captains"][-1]["xp"]
    by = {p["manager_id"]: p["payload"] for p in s.plans()}
    free, full, bad = by[1], by[2], by[404]
    assert free["kind"] == "free" and free["captain"]["id"] != free["vice"]["id"]
    assert free["xp"] > 0 and free["captain_options"]
    assert full["kind"] == "full" and len(full["xi"]) == 11 and len(full["bench"]) == 4
    assert full["captain"]["name"] and len(full["horizon"]) == 2
    assert all(set(t) == {"in", "out"} for t in full["transfers"])
    assert bad["kind"] == "error"
    assert "1 full plans, 1 free picks" in res.report()


def test_a_fresh_full_plan_is_kept_until_the_final_hours(env):  # noqa: F811
    recent = int((datetime.now(UTC) - timedelta(hours=1)).timestamp())
    m = [{"manager_id": 2, "tier": "pro", "kind": "full", "planned_at": recent}]
    s = FakeSite(m)
    res = sync(env[0].db, s, opts(), fpl=fpl(env))
    assert res.kept == 1 and res.full == 0 and s.plans() == []
    s = FakeSite(m)
    res = sync(env[0].db, s, opts(), fpl=fpl(env, hours=3))  # team news is in: re-plan
    assert res.full == 1


def test_the_full_plan_limit_gives_the_rest_the_free_pick(env):  # noqa: F811
    s = FakeSite([{"manager_id": 1, "tier": "pro"}, {"manager_id": 2, "tier": "pro"}])
    res = sync(env[0].db, s, opts(limit=1), fpl=fpl(env))
    assert (res.full, res.free) == (1, 1)


def test_outside_the_window_nothing_is_posted(env):  # noqa: F811
    s = FakeSite([{"manager_id": 1, "tier": "free"}])
    res = sync(env[0].db, s, opts(), fpl=fpl(env, hours=200))
    assert res.status == "skipped" and s.posts == [] and res.exit_code == 0


def test_an_unreachable_site_is_an_error(env):  # noqa: F811
    class Down(FakeSite):
        def managers(self, gw):
            raise ConnectionError

    res = sync(env[0].db, Down([]), opts(), fpl=fpl(env))
    assert res.status == "error" and res.exit_code == 2


def test_site_client_sends_the_token(monkeypatch):
    import httpx

    seen = []

    def handler(req):
        seen.append(req)
        if req.method == "GET":
            return httpx.Response(200, json={"managers": [{"manager_id": 7}]})
        return httpx.Response(200, json={"ok": True})

    c = SiteClient(
        "https://fplsim.test/", "tok", httpx.Client(transport=httpx.MockTransport(handler))
    )
    assert c.managers(5) == [{"manager_id": 7}]
    c.post("/api/engine/plans", {"plans": []})
    assert all(r.headers["Authorization"] == "Bearer tok" for r in seen)
    assert seen[0].url.params["gw"] == "5"
    assert SiteClient.from_env() is None
    monkeypatch.setenv("SITE_URL", "https://x")
    monkeypatch.setenv("ENGINE_TOKEN", "t")
    assert SiteClient.from_env() is not None


def test_cli_without_site_settings_is_a_no_op(capsys):
    assert cli.main(["site-sync"]) == 0
    assert "skipped" in capsys.readouterr().out
