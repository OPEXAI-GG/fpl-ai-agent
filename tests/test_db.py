import json
from datetime import datetime
from pathlib import Path

from fpl_agent.db import ingest_football_data, ingest_fpl, ingest_understat
from fpl_agent.db.database import FootballDB, team_key

FIX = Path(__file__).parent / "fixtures"


def load(name):
    return json.loads((FIX / name).read_text(encoding="utf-8"))


def test_team_keys_agree_across_sources():
    for names in (
        ["Man City", "Manchester City"],
        ["Man United", "Man Utd", "Manchester United"],
        ["Nott'm Forest", "Nottingham Forest"],
        ["Spurs", "Tottenham"],
        ["Wolves", "Wolverhampton Wanderers"],
    ):
        assert len({team_key(n) for n in names}) == 1, names


def test_one_match_across_three_sources():
    db = FootballDB()
    boot = load("bootstrap-static.json")
    ingest_fpl.load_bootstrap(db, boot, 2026, 7)
    ingest_fpl.load_fixtures(db, load("fixtures.json"), boot, 2026)
    ingest_understat.load_league(db, load("understat_league.json"), 2026)
    ingest_football_data.load_csv(
        db, (FIX / "fd_E0_2026.csv").read_text(encoding="utf-8"), "E0", 2026
    )
    # Arsenal v Coventry on 21 Aug is one canonical match, however many sources saw it
    m = db.query(
        "SELECT m.match_id, t.name h, a.name a FROM matches m "
        "JOIN teams t ON t.team_id = m.home_team_id JOIN teams a ON a.team_id = m.away_team_id "
        "WHERE m.match_date = DATE '2026-08-21'"
    )
    assert len(m) == 1 and m.loc[0, "h"] == "Arsenal"
    tm = db.query(
        "SELECT source, xg_for FROM team_match WHERE match_id = ?", [int(m.loc[0, "match_id"])]
    )
    assert set(tm.source) == {"understat", "football-data"}
    assert db.query("SELECT count(*) n FROM teams").loc[0, "n"] <= 20  # no duplicate clubs


def test_football_data_odds_and_xg():
    db = FootballDB()
    n = ingest_football_data.load_csv(
        db, (FIX / "fd_E1_2026.csv").read_text(encoding="utf-8"), "E1", 2026
    )
    assert n == 7
    odds = db.query("SELECT * FROM match_odds WHERE closing")
    assert len(odds) >= 7 and odds["home"].notna().all()
    xg = db.query("SELECT xg_for FROM team_match WHERE xg_for IS NOT NULL")
    assert len(xg) == 14
    assert db.query("SELECT count(*) n FROM matches WHERE competition_id = 'ENG2'").loc[0, "n"] == 7


def test_reingest_is_idempotent():
    db = FootballDB()
    text = (FIX / "fd_E1_2026.csv").read_text(encoding="utf-8")
    ingest_football_data.load_csv(db, text, "E1", 2026)
    first = db.counts()
    ingest_football_data.load_csv(db, text, "E1", 2026)
    again = db.counts()
    assert {k: v for k, v in first.items() if k != "ingest_log"} == {
        k: v for k, v in again.items() if k != "ingest_log"
    }


def test_fpl_snapshots_are_point_in_time():
    db = FootballDB()
    boot = load("bootstrap-static.json")
    t1, t2 = datetime(2026, 10, 1, 9), datetime(2026, 10, 2, 9)
    ingest_fpl.load_bootstrap(db, boot, 2026, 7, snapshot_at=t1)
    changed = json.loads(json.dumps(boot))
    changed["elements"][0]["now_cost"] += 1
    ingest_fpl.load_bootstrap(db, changed, 2026, 7, snapshot_at=t2)
    old = db.fpl_players_as_of(2026, 7, datetime(2026, 10, 1, 12))
    new = db.fpl_players_as_of(2026, 7, datetime(2026, 10, 3))
    fid = boot["elements"][0]["id"]
    assert old.loc[old.fpl_id == fid, "now_cost"].iloc[0] == boot["elements"][0]["now_cost"]
    assert new.loc[new.fpl_id == fid, "now_cost"].iloc[0] == boot["elements"][0]["now_cost"] + 1
    ids = {e["id"] for e in boot["elements"]}
    assert len(old) == len(new) == len(ids)


def test_live_points_keep_explain():
    db = FootballDB()
    ingest_fpl.load_bootstrap(db, load("bootstrap-static.json"), 2026, 1)
    n = ingest_fpl.load_live(db, load("live_gw1.json"), 2026, 1)
    assert n >= 6
    row = db.query("SELECT explain, total_points FROM fpl_points_gw LIMIT 1").iloc[0]
    assert json.loads(row["explain"])


def test_understat_player_links_and_shots():
    db = FootballDB()
    boot = load("bootstrap-static.json")
    ingest_fpl.load_bootstrap(db, boot, 2026, 7)
    data = load("understat_player.json")
    haaland = int(
        db.query("SELECT player_id FROM player_ids WHERE source = 'fpl-2026' LIMIT 1").iloc[0, 0]
    )
    ingest_understat.load_player(db, data, fpl_player_id=haaland)
    assert db.query("SELECT count(*) n FROM shots").loc[0, "n"] == 8
    sit = db.query("SELECT DISTINCT situation FROM shots")["situation"].tolist()
    assert sit  # shot situation kept for the set-piece / penalty split
    # player rows hang off the canonical id
    assert (
        db.query("SELECT count(*) n FROM player_ids WHERE player_id = ?", [haaland]).loc[0, "n"]
        == 2
    )


def test_parquet_roundtrip(tmp_path):
    db = FootballDB()
    ingest_football_data.load_csv(
        db, (FIX / "fd_E1_2026.csv").read_text(encoding="utf-8"), "E1", 2026
    )
    counts = db.export_parquet(tmp_path)
    db2 = FootballDB()
    db2.import_parquet(tmp_path)
    assert db2.counts()["matches"] == counts["matches"] == 7


def test_moved_fixture_does_not_duplicate():
    db = FootballDB()
    h = db.team_id("fpl", 1, "Arsenal")
    a = db.team_id("fpl", 2, "Chelsea")
    from datetime import date

    m1 = db.match_id("fpl", 10, "ENG1", 2026, date(2026, 11, 7), h, a)
    m2 = db.match_id("understat", "u9", "ENG1", 2026, date(2026, 11, 9), h, a)
    assert m1 == m2
    assert db.query("SELECT match_date FROM matches").loc[0, "match_date"].day == 9


def test_understat_player_appearances_link_to_known_matches():
    """A player's Understat match rows attach to the canonical match, club and stats."""
    db = FootballDB()
    league = {
        "dates": [
            {"id": "31222", "isResult": True, "datetime": "2026-09-20 15:30:00",
             "h": {"id": "88", "title": "Manchester City"}, "a": {"id": "5", "title": "Sunderland"},
             "goals": {"h": "5", "a": "3"}, "xG": {"h": "3.2", "a": "1.9"}}
        ]
    }  # fmt: skip
    ingest_understat.load_league(db, league, 2026)
    ingest_understat.load_player(db, load("understat_player.json"))
    app = db.query("SELECT * FROM appearances")
    assert len(app) == 1 and app.loc[0, "minutes"] == 90
    assert (
        db.query("SELECT name FROM teams WHERE team_id = ?", [int(app.loc[0, "team_id"])]).iloc[
            0, 0
        ]
        == "Manchester City"
    )
    stats = db.query("SELECT stat, value FROM player_match_stats").set_index("stat")["value"]
    assert stats["goals"] == 1 and round(stats["xG"], 2) == 1.34


def test_transaction_rolls_back_on_error():
    db = FootballDB()
    try:
        with db.transaction():
            db.team_id("fpl", 1, "Arsenal")
            raise RuntimeError("source died")
    except RuntimeError:
        pass
    assert db.counts()["teams"] == 0 and db.counts()["team_ids"] == 0


def test_club_names_that_differ_only_by_generic_words_are_one_club():
    db = FootballDB()
    a = db.team_id("football-data", "Bolton", "Bolton")
    assert db.team_id("fotmob", "1", "Bolton Wanderers") == a
    c = db.team_id("football-data", "Lincoln", "Lincoln")
    assert db.team_id("fotmob", "2", "Lincoln City") == c
    # different clubs that share a city stay apart
    city = db.team_id("fotmob", "3", "Bristol City")
    assert db.team_id("fotmob", "4", "Bristol Rovers") != city
    assert db.team_id("fotmob", "5", "Sheffield Wednesday") != db.team_id(
        "fotmob", "6", "Sheffield United"
    )


def test_dundee_and_dundee_united_stay_two_clubs():
    db = FootballDB()
    a = db.team_id("fotmob", "1", "Dundee")
    b = db.team_id("fotmob", "2", "Dundee United")
    assert a != b


def test_football_data_short_names_join_the_fotmob_club():
    db = FootballDB()
    assert db.team_id("football-data", "Bristol Rvs", "Bristol Rvs") == db.team_id(
        "fotmob", "1", "Bristol Rovers"
    )
    assert db.team_id("football-data", "Peterboro", "Peterboro") == db.team_id(
        "fotmob", "2", "Peterborough United"
    )
