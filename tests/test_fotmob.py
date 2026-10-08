"""FotMob: client behaviour, contract detection of changed formats and the ingest into the
football database, all on recorded real responses (Championship and Premier League)."""

import httpx
import pytest

from fpl_agent import contracts
from fpl_agent.data import leagues
from fpl_agent.data.fotmob import FotMobClient
from fpl_agent.db.database import FootballDB
from fpl_agent.db.ingest_fotmob import FotMobIngestor
from fpl_agent.errors import SourceFormatError, SourceUnavailable
from tests.helpers import fixture, fotmob_client

CH = leagues.get("ENG2")
PL = leagues.get("ENG1")


def loaded():
    db = FootballDB()
    ing = FotMobIngestor(db)
    ing.load_league(fixture("fotmob_league_championship.json"), CH, 2026)
    ing.load_match(fixture("fotmob_match_championship.json"), CH, 2026)
    return db, ing


def _full_name_starter(raw):
    return next(p for p in raw["content"]["lineup"]["homeTeam"]["starters"]
                if len(p["name"].split()) >= 2)  # fmt: skip


# ---- client -------------------------------------------------------------------------------
def test_client_fetches_league_and_match():
    fm = fotmob_client()
    assert contracts.fotmob_league(fm.league(48, "2026/2027")) == []
    assert contracts.fotmob_match(fm.match(5836754)) == []


def test_client_unknown_match_is_unavailable():
    with pytest.raises(SourceUnavailable):
        fotmob_client().match(1)


def test_client_html_is_a_format_error():
    fm = fotmob_client({"fotmob.com": (200, "<html>moved</html>")})
    with pytest.raises(SourceFormatError):
        fm.league(48)


def test_client_retries_server_errors_then_gives_up():
    calls = []

    def handler(request):
        calls.append(1)
        return httpx.Response(503)

    fm = FotMobClient(client=httpx.Client(transport=httpx.MockTransport(handler)),
                      backoff=0, retries=2, delay=0)  # fmt: skip
    with pytest.raises(SourceUnavailable):
        fm.league(48)
    assert len(calls) == 3


def test_client_transport_error_is_unavailable():
    def handler(request):
        raise httpx.ConnectError("down")

    fm = FotMobClient(client=httpx.Client(transport=httpx.MockTransport(handler)),
                      backoff=0, retries=1, delay=0)  # fmt: skip
    with pytest.raises(SourceUnavailable):
        fm.match(1)


def test_client_other_endpoints():
    fm = fotmob_client({"matches": (200, {"leagues": []}), "allLeagues": (200, {"popular": []})})
    assert fm.matches_on("20261003") == {"leagues": []}
    assert fm.all_leagues() == {"popular": []}


# ---- contracts: a changed format is caught before it reaches the database --------------------
@pytest.mark.parametrize("bad", [[], {}, {"details": {}}, "<html>", None])
def test_league_contract_rejects_garbage(bad):
    assert contracts.fotmob_league(bad)


def test_league_contract_detects_renamed_fields():
    raw = fixture("fotmob_league_championship.json")
    del raw["fixtures"]["allMatches"][0]["status"]["utcTime"]
    assert contracts.fotmob_league(raw)
    raw = fixture("fotmob_league_championship.json")
    raw["fixtures"]["allMatches"] = "oops"
    assert contracts.fotmob_league(raw)


@pytest.mark.parametrize("path", [("content", "lineup"), ("content", "playerStats"),
                                   ("general", "matchId"), ("header",)])  # fmt: skip
def test_match_contract_detects_missing_parts(path):
    raw = fixture("fotmob_match_championship.json")
    node = raw
    for k in path[:-1]:
        node = node[k]
    del node[path[-1]]
    assert contracts.fotmob_match(raw)
    with pytest.raises(SourceFormatError):
        contracts.require(contracts.fotmob_match(raw), "fotmob")


def test_match_contract_checks_lineup_and_player_entries():
    raw = fixture("fotmob_match_championship.json")
    del raw["content"]["lineup"]["homeTeam"]["starters"]
    assert contracts.fotmob_match(raw)
    raw = fixture("fotmob_match_championship.json")
    first = next(iter(raw["content"]["playerStats"].values()))
    del first["stats"]
    assert contracts.fotmob_match(raw)


# ---- ingest ----------------------------------------------------------------------------------
def test_league_load_creates_matches_with_results():
    db, _ = loaded()
    c = db.counts()
    assert c["matches"] == 9  # 6 finished + 3 upcoming
    assert db.query("SELECT count(*) n FROM matches WHERE finished").n[0] >= 6
    row = db.query(
        "SELECT home_goals, away_goals FROM matches m JOIN match_ids i USING (match_id) "
        "WHERE i.source_id = '5836754'"
    ).iloc[0]
    assert (row.home_goals, row.away_goals) == (2, 2)
    assert db.query("SELECT count(*) n FROM competitions WHERE competition_id = 'ENG2'").n[0] == 1


def test_match_load_writes_team_xg_appearances_stats_and_shots():
    db, _ = loaded()
    tm = db.query("SELECT * FROM team_match WHERE source = 'fotmob' ORDER BY is_home DESC")
    assert len(tm) == 2
    assert tm.xg_for.tolist() == pytest.approx([1.97, 0.84])
    assert tm.xg_against.tolist() == pytest.approx([0.84, 1.97])
    assert tm.goals_for.tolist() == [2, 2]
    assert tm.shots.tolist() == [18, 10]
    apps = db.query("SELECT * FROM appearances")
    assert apps.started.sum() == 22
    assert apps.minutes.between(0, 125).all()
    assert (apps.groupby("team_id").started.sum() == 11).all()
    assert set(apps.position.dropna()) <= {"GKP", "DEF", "MID", "FWD"}
    shots = db.query("SELECT * FROM shots")
    assert len(shots) > 10 and shots.xg.notna().all()
    assert shots.xg.sum() == pytest.approx(2.81, abs=0.3)  # matches team xG within own goals etc.


def test_substitute_minutes_and_unused_subs():
    db, _ = loaded()
    apps = db.query("SELECT * FROM appearances WHERE NOT started")
    assert (apps.minutes == 0).any()  # unused substitutes are kept: bench information
    assert (apps.minutes > 0).any()
    subbed_off = db.query("SELECT * FROM appearances WHERE started AND minutes < 90")
    assert len(subbed_off) >= 2


def test_defcon_proxies_are_computed():
    db, _ = loaded()
    s = db.query(
        "SELECT player_id, stat, value FROM player_match_stats WHERE stat IN ('cbit','cbirt', "
        "'clearances','blocks','interceptions','tackles','recoveries')"
    )
    wide = s.pivot(index="player_id", columns="stat", values="value").fillna(0)
    assert ((wide.clearances + wide.blocks + wide.interceptions + wide.tackles) == wide.cbit).all()
    assert (wide.cbirt >= wide.cbit).all()
    assert wide.cbit.max() > 3  # somebody defended


def test_goals_and_cards_recorded():
    db, _ = loaded()
    goals = db.query("SELECT sum(value) g FROM player_match_stats WHERE stat = 'goals'").g[0]
    assert goals == 4
    yellows = db.query("SELECT sum(value) y FROM player_match_stats WHERE stat = 'yellow_cards'").y[
        0
    ]
    assert yellows == 4


def test_reload_is_idempotent():
    db, ing = loaded()
    before = {k: v for k, v in db.counts().items() if k != "ingest_log"}
    ing.load_league(fixture("fotmob_league_championship.json"), CH, 2026)
    ing.load_match(fixture("fotmob_match_championship.json"), CH, 2026)
    assert {k: v for k, v in db.counts().items() if k != "ingest_log"} == before


def test_unfinished_match_is_skipped():
    db = FootballDB()
    raw = fixture("fotmob_match_championship.json")
    raw["general"]["finished"] = False
    assert FotMobIngestor(db).load_match(raw, CH, 2026) == 0
    assert db.counts()["appearances"] == 0


def test_match_without_xg_leaves_nulls_not_zeros():
    db = FootballDB()
    raw = fixture("fotmob_match_championship.json")
    groups = raw["content"]["stats"]["Periods"]["All"]["stats"]
    for g in groups:
        g["stats"] = [s for s in g["stats"] if s["key"] != "expected_goals"]
    FotMobIngestor(db).load_match(raw, CH, 2026)
    xg = db.query("SELECT xg_for, xg_against FROM team_match")
    assert xg.xg_for.isna().all() and xg.xg_against.isna().all()


def test_players_link_to_existing_canonical_ids_when_unambiguous():
    db = FootballDB()
    raw = fixture("fotmob_match_premier.json")
    star = _full_name_starter(raw)
    pid = db.player_id("fpl", 1, star["name"], "MID")
    FotMobIngestor(db).load_match(raw, PL, 2026)
    row = db.con.execute(
        "SELECT player_id FROM player_ids WHERE source = 'fotmob' AND source_id = ?",
        [str(star["id"])],
    ).fetchone()
    assert row[0] == pid
    assert db.query("SELECT count(*) n FROM players WHERE name = ?", [star["name"]]).n[0] == 1


def test_ambiguous_names_are_not_merged():
    db = FootballDB()
    raw = fixture("fotmob_match_premier.json")
    star = _full_name_starter(raw)
    db.player_id("fpl", 1, star["name"], "MID")
    db.player_id("understat", 99, star["name"], "MID")  # two different people with that name
    FotMobIngestor(db).load_match(raw, PL, 2026)
    assert db.query("SELECT count(*) n FROM players WHERE name = ?", [star["name"]]).n[0] == 3


def test_premier_league_match_links_to_existing_fixture_by_team_and_season():
    db = FootballDB()
    home = fixture("fotmob_match_premier.json")["general"]["homeTeam"]["name"]
    away = fixture("fotmob_match_premier.json")["general"]["awayTeam"]["name"]
    h, a = db.team_id("fpl", 1, home), db.team_id("fpl", 2, away)
    mid = db.match_id("fpl", 1, "ENG1", 2026, __import__("datetime").date(2026, 8, 22), h, a)
    FotMobIngestor(db).load_match(fixture("fotmob_match_premier.json"), PL, 2026)
    assert db.query("SELECT count(*) n FROM matches").n[0] == 1
    assert db.query("SELECT count(*) n FROM team_match WHERE match_id = ?", [mid]).n[0] == 2


def test_cup_ties_with_the_same_pairing_are_kept_apart():
    db = FootballDB()
    ing = FotMobIngestor(db)
    cup = leagues.get("UCL")
    for i, day in enumerate(("2026-09-16T19:00:00Z", "2026-10-21T19:00:00Z")):
        ing.load_league(
            {"fixtures": {"allMatches": [{
                "id": str(900 + i), "round": "1",
                "home": {"id": "1", "name": "Alpha"}, "away": {"id": "2", "name": "Beta"},
                "status": {"utcTime": day, "finished": True, "scoreStr": "1 - 0"}}]}},
            cup, 2026,
        )  # fmt: skip
    assert db.query("SELECT count(*) n FROM matches").n[0] == 2


def test_cancelled_fixtures_are_ignored():
    db = FootballDB()
    ing = FotMobIngestor(db)
    ing.load_league(
        {"fixtures": {"allMatches": [{
            "id": "1", "home": {"id": "1", "name": "A"}, "away": {"id": "2", "name": "B"},
            "status": {"utcTime": "2026-09-01T19:00:00Z", "finished": False, "cancelled": True}}]}},
        CH, 2026,
    )  # fmt: skip
    assert db.counts()["matches"] == 0


def test_league_registry():
    assert leagues.get("ENG2").fotmob_id == 48
    assert leagues.get("ENG2").fotmob_season(2026) == "2026/2027"
    assert leagues.get("BRA1").fotmob_season(2026) == "2026"
    with pytest.raises(KeyError):
        leagues.get("XXX1")


def test_playoff_matches_do_not_merge_into_league_fixtures():
    """Championship play-offs repeat league pairings; they must not overwrite league results."""
    db = FootballDB()
    ing = FotMobIngestor(db)
    home, away = {"id": "1", "name": "Aston Villa"}, {"id": "2", "name": "Fulham"}
    league_match = {"id": "1", "round": "40", "home": home, "away": away,
                    "status": {"utcTime": "2018-03-01T19:45:00Z", "finished": True,
                               "scoreStr": "0 - 1"}}  # fmt: skip
    playoff = {
        **league_match,
        "id": "2",
        "round": "final",
        "status": {"utcTime": "2018-05-26T15:00:00Z", "finished": True, "scoreStr": "2 - 1"},
    }
    ing.load_league({"fixtures": {"allMatches": [league_match, playoff]}}, CH, 2017)
    rows = db.query("SELECT home_goals, away_goals FROM matches")
    assert len(rows) == 1 and rows.home_goals[0] == 0


def test_club_aliases_between_sources():
    from fpl_agent.db.database import team_key

    assert team_key("Sheffield Weds") == team_key("Sheffield Wednesday")
    assert team_key("Nott'm Forest") == team_key("Nottingham Forest")
