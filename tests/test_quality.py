"""Each data-quality check really fires on corrupted data, and the clean fixture DB is clean."""

import json

import pytest

from fpl_agent.db import ingest_football_data, ingest_fpl, ingest_understat
from fpl_agent.db.database import FootballDB
from fpl_agent.db.quality import errors, format_issues, quarantine, run_checks
from tests.helpers import FIX, fixture, live_payload


@pytest.fixture
def db():
    db = FootballDB()
    boot = fixture("bootstrap-static.json")
    ingest_fpl.load_bootstrap(db, boot, 2026, 7)
    ingest_fpl.load_fixtures(db, fixture("fixtures.json"), boot, 2026)
    ingest_fpl.load_live(db, live_payload(boot), 2026, 1)
    ingest_understat.load_league(db, fixture("understat_league.json"), 2026)
    ingest_understat.load_player(db, fixture("understat_player.json"), 1)
    ingest_football_data.load_csv(db, (FIX / "fd_E0_2026.csv").read_text(), "E0", 2026)
    ingest_football_data.load_csv(db, (FIX / "fd_E1_2026.csv").read_text(), "E1", 2026)
    return db


def names(db):
    return {i.check for i in run_checks(db)}


def test_clean_database_has_no_errors(db):
    assert errors(run_checks(db)) == [], format_issues(run_checks(db))


CORRUPTIONS = [
    ("same_team_match", "UPDATE matches SET away_team_id = home_team_id WHERE match_id = 1"),
    ("finished_without_score",
     "UPDATE matches SET finished = true, home_goals = NULL WHERE match_id = 1"),
    ("negative_or_huge_goals", "UPDATE team_match SET goals_for = 40 WHERE match_id = (SELECT min(match_id) FROM team_match)"),
    ("bad_xg", "UPDATE team_match SET xg_for = -1 WHERE match_id = (SELECT min(match_id) FROM team_match)"),
    ("bad_minutes", "INSERT INTO appearances (match_id, player_id, source, minutes) SELECT min(m.match_id), min(p.player_id), 'x', 400 FROM matches m, players p"),
    ("bad_shots", "UPDATE shots SET xg = 1.7"),
    ("bad_prices", "UPDATE fpl_players_gw SET now_cost = 5"),
    ("bad_chance", "UPDATE fpl_players_gw SET chance_next = 150 WHERE fpl_id = (SELECT min(fpl_id) FROM fpl_players_gw)"),
    ("bad_status", "UPDATE fpl_players_gw SET status = 'zzz' WHERE fpl_id = (SELECT min(fpl_id) FROM fpl_players_gw)"),
    ("odds_below_one", "UPDATE match_odds SET home = 0.5"),
    ("orphan_team_match.match_id", "DELETE FROM matches WHERE match_id = (SELECT min(match_id) FROM team_match)"),
    ("orphan_fpl_players_gw.player_id", "DELETE FROM players WHERE player_id = (SELECT min(player_id) FROM fpl_players_gw)"),
    ("fpl_points_unlinked", "UPDATE fpl_points_gw SET player_id = NULL"),
    ("points_vs_rules", "UPDATE fpl_points_gw SET total_points = total_points + 1 WHERE fpl_id = (SELECT min(fpl_id) FROM fpl_points_gw)"),
    ("duplicate_team_keys", "INSERT INTO teams VALUES (9999, 'Arsenal again', (SELECT key FROM teams LIMIT 1))"),
]  # fmt: skip


@pytest.mark.parametrize("check,sql", CORRUPTIONS, ids=[c[0] for c in CORRUPTIONS])
def test_corruption_is_detected(db, check, sql):
    db.con.execute(sql)
    assert check in names(db), format_issues(run_checks(db))


def test_duplicate_league_pairing_detected(db):
    row = db.con.execute(
        "SELECT competition_id, season, home_team_id, away_team_id FROM matches LIMIT 1"
    ).fetchone()
    db.con.execute(
        "INSERT INTO matches (match_id, competition_id, season, home_team_id, away_team_id) "
        "VALUES (99999, ?, ?, ?, ?)",
        list(row),
    )
    assert "duplicate_matches" in names(db)


def test_sources_disagreeing_on_a_score_is_detected(db):
    db.con.execute(
        "UPDATE matches SET home_goals = 9 WHERE finished AND match_id = "
        "(SELECT m.match_id FROM matches m JOIN team_match t USING (match_id) WHERE m.finished LIMIT 1)"
    )
    assert "score_disagreement" in names(db)


def test_issue_formatting(db):
    db.con.execute("UPDATE shots SET xg = 2")
    assert "bad_shots" in format_issues(run_checks(db))
    assert json.dumps(format_issues([])) == '"no issues"'


def test_quarantine_drops_bad_rows_and_names_them(db):
    db.con.execute("UPDATE shots SET xg = 2 WHERE shot_id = (SELECT min(shot_id) FROM shots)")
    db.con.execute(
        "UPDATE fpl_players_gw SET now_cost = 7 WHERE fpl_id = (SELECT min(fpl_id) FROM fpl_players_gw)"
    )
    db.con.execute("UPDATE matches SET away_team_id = home_team_id WHERE match_id = 1")
    before = db.counts()
    out = quarantine(db)
    assert {i.check for i in out} == {
        "quarantined_shots",
        "quarantined_prices",
        "quarantined_match",
    }
    assert all(i.severity == "warn" for i in out)
    assert any("vs itself" in i.detail for i in out)
    after = db.counts()
    assert after["matches"] == before["matches"] - 1 and after["shots"] < before["shots"]
    assert errors(run_checks(db)) == [], format_issues(run_checks(db))


def test_quarantine_does_nothing_on_clean_data(db):
    before = db.counts()
    assert quarantine(db) == []
    assert db.counts() == before


def test_a_few_score_disagreements_are_only_a_warning_in_a_big_database(db):
    db.con.execute(
        "UPDATE matches SET home_goals = 9 WHERE match_id = (SELECT min(match_id) FROM matches WHERE finished)"
    )
    for i in range(2000):  # a large body of agreeing matches
        db.con.execute(
            "INSERT INTO matches (match_id, competition_id, season, home_team_id, away_team_id, "
            "finished) VALUES (?, 'XXX', 2000, 1, 2, true)",
            [500000 + i],
        )
    found = [i for i in run_checks(db) if i.check == "score_disagreement"]
    assert found and found[0].severity == "warn"
