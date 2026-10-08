"""Odds API ingestion and the scout: both must be inert without keys and bounded with them."""

import json

import httpx
import pandas as pd

from fpl_agent.data import oddsapi
from fpl_agent.db.database import FootballDB
from fpl_agent.scout import Adjustment, Scout, parse


def make_db():
    db = FootballDB()
    db.con.execute("INSERT INTO competitions VALUES ('ENG1', 'Premier League', 'ENG', 1)")
    h = db.team_id("fpl", 1, "Arsenal")
    a = db.team_id("fpl", 2, "Manchester City")
    from datetime import date, datetime

    mid = db.match_id("fpl", 1, "ENG1", 2026, date(2026, 10, 17), h, a,
                      kickoff=datetime(2026, 10, 17, 12, 30), finished=False)  # fmt: skip
    return db, mid


EVENT = {
    "home_team": "Arsenal", "away_team": "Manchester City", "commence_time": "2026-10-17T12:30:00Z",
    "bookmakers": [
        {"markets": [
            {"key": "h2h", "outcomes": [{"name": "Arsenal", "price": 2.6},
                                        {"name": "Manchester City", "price": 2.7},
                                        {"name": "Draw", "price": 3.4}]},
            {"key": "totals", "outcomes": [{"name": "Over", "point": 2.5, "price": 1.8},
                                           {"name": "Under", "point": 2.5, "price": 2.0}]}]},
        {"markets": [
            {"key": "h2h", "outcomes": [{"name": "Arsenal", "price": 2.8},
                                        {"name": "Manchester City", "price": 2.5},
                                        {"name": "Draw", "price": 3.6}]}]},
    ],
}


def test_odds_are_attached_to_the_upcoming_match_as_a_consensus():
    db, mid = make_db()
    assert oddsapi.load_events(db, [EVENT]) == 1
    r = db.query("SELECT * FROM match_odds").iloc[0]
    assert r.match_id == mid and abs(r.home - 2.7) < 1e-9 and abs(r.over25 - 1.8) < 1e-9


def test_unknown_teams_and_finished_matches_are_skipped():
    db, _ = make_db()
    ev = {**EVENT, "home_team": "Nobody FC"}
    assert oddsapi.load_events(db, [ev]) == 0
    db.con.execute("UPDATE matches SET finished = true")
    assert oddsapi.load_events(db, [EVENT]) == 0


def test_odds_client_is_disabled_without_a_key(monkeypatch):
    monkeypatch.delenv("ODDS_API_KEY", raising=False)
    c = oddsapi.OddsClient()
    assert not c.enabled


def test_odds_client_fetches_with_a_key():
    def handler(req):
        assert req.url.params["apiKey"] == "k"
        return httpx.Response(200, json=[EVENT])

    c = oddsapi.OddsClient("k", httpx.Client(transport=httpx.MockTransport(handler)))
    assert c.upcoming()[0]["home_team"] == "Arsenal"


POOL = pd.DataFrame({
    "player_id": [1, 2, 3, 4], "status": ["d", "i", "a", "n"], "chance": [50.0, 0.0, 100.0, 0.0],
    "news": ["Knock - 50%", "Hamstring", "", "Left club"], "price": [100, 90, 80, 70],
})


def test_scout_is_a_no_op_without_a_key(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    s = Scout()
    out, adj = s.run(POOL)
    assert adj == [] and out.equals(POOL)


def test_scout_selects_only_players_with_news():
    s = Scout(key="k")
    assert set(s.select(POOL)["player_id"]) == {1, 2, 4}


def test_parse_drops_malformed_and_unknown_entries():
    text = 'Sure: [{"id": 1, "p_play": 0.9, "reason": "back in training"}, {"id": 9, "p_play": 0.5}, ' \
           '{"id": 2, "p_play": 1.7}, {"id": "x"}]'
    out = parse(text, {1, 2})
    assert out == [Adjustment(1, 0.9, "back in training")]
    assert parse("no json", {1}) == [] and parse("[not json]", {1}) == []


def test_scout_moves_availability_only_within_bounds():
    s = Scout(key="k", max_shift=0.35)
    out = s.apply(POOL, [Adjustment(1, 1.0, ""), Adjustment(2, 1.0, ""), Adjustment(4, 1.0, "")])
    chance = dict(zip(out["player_id"], out["chance"], strict=True))
    assert chance[1] == 85.0  # 50% + at most 35 points
    assert chance[2] == 35.0
    assert chance[4] == 0.0  # a player who left is never resurrected


def test_scout_end_to_end_with_a_mocked_api():
    def handler(req):
        sent = json.loads(req.content)
        assert sent["max_tokens"] <= 1500 and req.headers["x-api-key"] == "k"
        return httpx.Response(200, json={"content": [{"type": "text", "text":
                              '[{"id": 1, "p_play": 0.7, "reason": "trained Friday"}]'}]})

    s = Scout(key="k", client=httpx.Client(transport=httpx.MockTransport(handler)))
    out, adj = s.run(POOL)
    assert adj[0].p_play == 0.7 and out.loc[out.player_id == 1, "chance"].iloc[0] == 70.0


def test_scout_failure_returns_the_pool_unchanged():
    def handler(req):
        return httpx.Response(500)

    s = Scout(key="k", client=httpx.Client(transport=httpx.MockTransport(handler)))
    out, adj = s.run(POOL)
    assert adj == [] and out.equals(POOL)
