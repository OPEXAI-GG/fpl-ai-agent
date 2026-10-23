"""Each data-quality check really fires on corrupted data, and the clean fixture DB is clean."""

import json
from datetime import date

import pytest

from fpl_agent.db import ingest_football_data, ingest_fpl, ingest_understat
from fpl_agent.db.database import FootballDB
from fpl_agent.db.quality import (
    blocked_writes,
    errors,
    format_issues,
    quarantine,
    run_checks,
    table_digests,
)
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
    ingest_football_data.load_csv(
        db, (FIX / "fd_E0_2026.csv").read_text(encoding="utf-8"), "E0", 2026
    )
    ingest_football_data.load_csv(
        db, (FIX / "fd_E1_2026.csv").read_text(encoding="utf-8"), "E1", 2026
    )
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
    found = next(i for i in run_checks(db) if i.check == "score_disagreement")
    assert " says " in found.detail and "9-" in found.detail  # names where, with an example


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


def _disagree(db, n):
    db.con.execute(
        "UPDATE matches SET home_goals = 9 WHERE match_id IN (SELECT DISTINCT m.match_id FROM "
        "matches m JOIN team_match t USING (match_id) WHERE m.finished ORDER BY 1 LIMIT ?)",
        [n],
    )


def test_quarantine_drops_a_few_matches_whose_sources_disagree(db):
    _disagree(db, 1)
    before = db.counts()["matches"]
    out = [i for i in quarantine(db) if i.check == "quarantined_scores"]
    assert out and out[0].severity == "warn" and "dropped 1 matches" in out[0].detail
    assert " says " in out[0].detail  # names where, so the cause can still be found
    assert db.counts()["matches"] == before - 1
    assert "score_disagreement" not in names(db)


def test_a_broken_source_is_not_quarantined_away(db):
    finished = db.con.execute("SELECT count(*) FROM matches WHERE finished").fetchone()[0]
    _disagree(db, max(2, finished // 5))  # far beyond a few corrections
    before = db.counts()["matches"]
    assert not [i for i in quarantine(db) if i.check == "quarantined_scores"]
    assert db.counts()["matches"] == before
    assert "score_disagreement" in {i.check for i in errors(run_checks(db))}


def test_repair_clears_merged_split_season_matches_and_wrong_club_links():
    from fpl_agent.db.quality import repair

    db = FootballDB()
    du = db.team_id("fotmob", "1", "Dundee United")
    db.con.execute(  # what older code stored: a second source's Dundee linked to Dundee United
        "INSERT INTO team_ids VALUES ('vaastav-team', 'x', ?, 'Dundee')", [du]
    )
    other = db.team_id("fotmob", "2", "Celtic")
    keep = db.team_id("fotmob", "3", "Arsenal")
    mid = db.match_id("fotmob", 10, "SCO1", 2024, date(2024, 9, 1), du, other, home_goals=1,
                      away_goals=0, finished=True)  # fmt: skip
    eng = db.match_id("fotmob", 11, "ENG1", 2024, date(2024, 9, 1), keep, other)
    db.con.execute("INSERT INTO ingest_state VALUES ('fotmob', '10', 'done', 'SCO1 2024', now())")
    db.con.execute("INSERT INTO ingest_state VALUES ('fotmob', '11', 'done', 'ENG1 2024', now())")
    found = repair(db)
    assert found[0].severity == "warn" and "1 BEL1/SCO1 matches" in found[0].detail
    assert "1 wrong club links" in found[0].detail
    assert db.query("SELECT match_id FROM matches").match_id.tolist() == [eng]
    assert mid not in db.query("SELECT match_id FROM match_ids").match_id.tolist()
    assert db.query("SELECT key FROM ingest_state WHERE source = 'fotmob'").key.tolist() == ["11"]
    assert (
        db.con.execute("SELECT count(*) FROM team_ids WHERE source = 'vaastav-team'").fetchone()[0]
        == 0
    )
    assert db.team_id("vaastav-team", "x", "Dundee") != du  # made again, correctly
    assert repair(db) == []  # once per database


def test_repair_merges_a_club_stored_twice_and_its_doubled_matches():
    from fpl_agent.db.quality import repair, run_checks

    db = FootballDB()
    mk = db.team_id("fotmob", "1", "Milton Keynes Dons")
    # what older code stored before the alias: one club under two keys
    db.con.execute("INSERT INTO teams VALUES (90, 'Peterboro', 'peterboro')")
    db.con.execute("INSERT INTO team_ids VALUES ('football-data', 'Peterboro', 90, 'Peterboro')")
    db.con.execute("INSERT INTO teams VALUES (91, 'Peterborough United', 'peterborough united')")
    db.con.execute("INSERT INTO team_ids VALUES ('fotmob', '8677', 91, 'Peterborough United')")
    fd = db.match_id("football-data", "x", "ENG3", 2026, date(2026, 9, 12), mk, 90,
                     home_goals=1, away_goals=1, finished=True)  # fmt: skip
    fm = db.match_id("fotmob", 5, "ENG3", 2026, date(2026, 9, 12), mk, 91, matchday=6)
    for mid, tid, src in [(fd, 90, "football-data"), (fm, 91, "fotmob")]:
        db.con.execute(
            "INSERT INTO team_match (match_id, team_id, source, is_home, goals_for, "
            "goals_against) VALUES (?, ?, ?, false, 1, 1)",
            [mid, tid, src],
        )
    found = repair(db)
    assert any(i.check == "merged_teams" and "Peterborough United" in i.detail for i in found)
    assert any(i.check == "merged_matches" for i in found)
    assert db.query("SELECT team_id, key FROM teams WHERE team_id >= 90").values.tolist() == [
        [90, "peterborough"]
    ]
    m = db.query("SELECT * FROM matches WHERE competition_id = 'ENG3'")
    assert len(m) == 1 and m.matchday[0] == 6 and m.home_goals[0] == 1 and m.away_team_id[0] == 90
    assert sorted(db.query("SELECT source FROM team_match").source) == ["football-data", "fotmob"]
    assert set(db.query("SELECT match_id FROM match_ids").match_id) == {fd}
    assert db.team_id("fotmob", "8677", "Peterborough United") == 90
    assert db.team_id("fotmob", "new", "Peterborough") == 90
    assert not [i for i in run_checks(db) if i.severity == "error"]
    assert not [i for i in repair(db) if i.check.startswith("merged")]  # nothing left to merge


def test_legacy_double_gameweek_copies_are_reported():
    from fpl_agent.db.database import FootballDB

    db = FootballDB()
    stats = '{"minutes": 180, "total_points": 8}'
    for fx in (1, 2):  # older code copied the GW totals into each fixture row
        db.con.execute(
            "INSERT INTO fpl_points_gw (season, gw, fpl_id, fixture_id, minutes, total_points, "
            "stats, explain) VALUES (2026, 3, 7, ?, 180, 4, ?, '[{}]')",
            [fx, stats],
        )
    found = [i for i in run_checks(db) if i.check == "dgw_history_legacy"]
    assert found and found[0].severity == "warn" and found[0].detail.startswith("1 ")
    db.con.execute("INSERT INTO ingest_state VALUES ('fpl_points', '2026:3', 'final', '', NULL)")
    assert not [i for i in run_checks(db) if i.check == "dgw_history_legacy"]


def test_blocked_quarantine_reports_the_change_and_never_writes(db):
    db.con.execute("UPDATE shots SET xg = 2 WHERE shot_id = (SELECT min(shot_id) FROM shots)")
    before = table_digests(db)
    out = blocked_writes(db, quarantine, "quarantine")
    assert [i.check for i in out] == ["quarantine_blocked"] and out[0].severity == "error"
    assert "shots" in out[0].detail and "quarantined_shots" not in out[0].check
    assert table_digests(db) == before  # the real database is untouched
    assert db.con.execute("SELECT current_database()").fetchone()[0] != "repair_probe"
    assert not db.con.execute(
        "SELECT 1 FROM duckdb_databases() WHERE database_name = 'repair_probe'"
    ).fetchall()


def test_blocked_quarantine_is_silent_on_clean_data(db):
    assert blocked_writes(db, quarantine, "quarantine") == []


def test_a_failing_probe_still_restores_the_connection(db):
    def boom(d):
        d.con.execute("DELETE FROM shots")
        raise RuntimeError("probe failed")

    before = table_digests(db)
    with pytest.raises(RuntimeError):
        blocked_writes(db, boom, "quarantine")
    assert table_digests(db) == before
    assert db.con.execute("SELECT current_database()").fetchone()[0] != "repair_probe"


# ---- reversible repairs (R2) -----------------------------------------------------------------
def test_reference_registry_matches_the_declared_schema():
    """Every canonical-id column in the schema is either in REFERENCES (so merges and deletes
    reach it) or named in NOT_REFERENCES with a reason."""
    from fpl_agent.db.database import declared_columns
    from fpl_agent.db.quality import NOT_REFERENCES, REFERENCES

    ids = {"match_id", "team_id", "player_id", "home_team_id", "away_team_id", "from_team_id",
           "to_team_id", "canonical_id"}  # fmt: skip
    found = {(t, c) for t, cols in declared_columns().items() for c in cols if c in ids}
    listed = {r for refs in REFERENCES.values() for r in refs}
    assert found == listed | set(NOT_REFERENCES)
    assert not listed & set(NOT_REFERENCES)


def _orphans(db):
    from fpl_agent.db.quality import REFERENCES

    parents = {"match": ("matches", "match_id"), "team": ("teams", "team_id"),
               "player": ("players", "player_id")}  # fmt: skip
    out = {}
    for kind, refs in REFERENCES.items():
        pt, pc = parents[kind]
        for t, c in refs:
            if (t, c) == (pt, pc):
                continue
            n = db.con.execute(f"SELECT count(*) FROM {t} WHERE {c} IS NOT NULL AND {c} NOT IN "
                               f"(SELECT {pc} FROM {pt})").fetchone()[0]  # fmt: skip
            if n:
                out[f"{t}.{c}"] = n
    return out


def _data(db):
    return {t: d for t, d in table_digests(db).items() if t != "repair_log"}


def _journal_id(db):
    return db.con.execute(
        "SELECT DISTINCT repair_id FROM repair_log WHERE rolled_back_at IS NULL "
        "ORDER BY 1 DESC LIMIT 1"
    ).fetchone()[0]


def _log_odds(db, mid):
    db.con.execute(
        "INSERT INTO match_odds_log (match_id, source, bookmaker, closing, home, ingested_at) "
        "VALUES (?, 'fd', 'b365', true, 2.1, '2026-09-01')",
        [mid],
    )


def test_quarantine_removes_every_dependent_and_rolls_back_exactly(db):
    from fpl_agent.db.quality import rollback_repair

    db.con.execute("UPDATE matches SET away_team_id = home_team_id WHERE match_id = 1")
    _log_odds(db, 1)
    db.con.execute("UPDATE shots SET xg = 2 WHERE shot_id = (SELECT min(shot_id) FROM shots)")
    before = _data(db)
    assert quarantine(db)
    assert _orphans(db) == {}  # match_odds_log included
    left = db.con.execute("SELECT count(*) FROM match_odds_log WHERE match_id = 1").fetchone()
    assert left == (0,)
    restored = rollback_repair(db, _journal_id(db))
    assert restored["matches"] == 1 and restored["match_odds_log"] >= 1 and restored["shots"] >= 1
    assert _data(db) == before


def _split_club():
    """The merge test's set-up, plus references a merge must also move."""
    db = FootballDB()
    mk = db.team_id("fotmob", "1", "Milton Keynes Dons")
    db.con.execute("INSERT INTO teams VALUES (90, 'Peterboro', 'peterboro')")
    db.con.execute("INSERT INTO team_ids VALUES ('football-data', 'Peterboro', 90, 'Peterboro')")
    db.con.execute("INSERT INTO teams VALUES (91, 'Peterborough United', 'peterborough united')")
    db.con.execute("INSERT INTO team_ids VALUES ('fotmob', '8677', 91, 'Peterborough United')")
    fd = db.match_id("football-data", "x", "ENG3", 2026, date(2026, 9, 12), mk, 90,
                     home_goals=1, away_goals=1, finished=True)  # fmt: skip
    fm = db.match_id("fotmob", 5, "ENG3", 2026, date(2026, 9, 12), mk, 91, matchday=6)
    for mid, tid, src in [(fd, 90, "football-data"), (fm, 91, "fotmob"),
                          (fm, 91, "football-data")]:  # fmt: skip
        db.con.execute(
            "INSERT INTO team_match (match_id, team_id, source, is_home, goals_for, "
            "goals_against) VALUES (?, ?, ?, false, 1, 1)",
            [mid, tid, src],
        )
    _log_odds(db, fm)
    db.con.execute(
        "INSERT INTO transfers (player_id, from_team_id, to_team_id, source) "
        "VALUES (NULL, 91, ?, 't')",
        [mk],
    )
    return db, fd, fm


def test_a_merge_moves_every_reference_and_rolls_back_exactly():
    from fpl_agent.db.quality import repair, rollback_repair

    db, fd, fm = _split_club()
    before = _data(db)
    found = repair(db)
    assert any(i.check == "merged_matches" for i in found)
    assert _orphans(db) == {}
    assert db.con.execute("SELECT match_id FROM match_odds_log").fetchall() == [(fd,)]
    assert db.con.execute("SELECT from_team_id FROM transfers").fetchall() == [(90,)]
    # the colliding copy of a team_match row is journalled, not lost
    kept = db.con.execute(
        "SELECT count(*) FROM repair_log WHERE table_name = 'team_match' AND action = 'removed'"
    ).fetchone()[0]
    assert kept >= 1
    ids = db.con.execute("SELECT DISTINCT repair_id FROM repair_log ORDER BY 1 DESC").fetchall()
    for (rid,) in ids:
        rollback_repair(db, rid)
    assert _data(db) == before


def test_rollback_refuses_after_an_intervening_change_and_writes_nothing():
    from fpl_agent.db.quality import RepairConflict, merge_split_teams, rollback_repair

    db, _, _ = _split_club()
    merge_split_teams(db)
    db.con.execute("UPDATE match_odds_log SET home = 9.9")  # someone changed a moved row since
    state = table_digests(db)
    with pytest.raises(RepairConflict, match="changed since"):
        rollback_repair(db, _journal_id(db))
    assert table_digests(db) == state


def test_rollback_refuses_a_key_collision_and_a_second_rollback():
    from fpl_agent.db.quality import RepairConflict, rollback_repair

    db = FootballDB()
    db.con.execute("INSERT INTO teams VALUES (1, 'A', 'a'), (2, 'B', 'b')")
    db.con.execute("INSERT INTO matches (match_id, home_team_id, away_team_id) VALUES (7, 1, 1)")
    quarantine(db)
    rid = _journal_id(db)
    db.con.execute("INSERT INTO matches (match_id, home_team_id, away_team_id) VALUES (7, 1, 2)")
    state = table_digests(db)
    with pytest.raises(RepairConflict, match="collides"):
        rollback_repair(db, rid)
    assert table_digests(db) == state
    db.con.execute("DELETE FROM matches WHERE match_id = 7")
    rollback_repair(db, rid)
    with pytest.raises(RepairConflict, match="no journalled change"):
        rollback_repair(db, rid)


def test_an_interrupted_merge_changes_nothing(monkeypatch):
    from fpl_agent.db import quality

    db, _, _ = _split_club()
    before = table_digests(db)
    calls = {"n": 0}
    real = quality.Journal.move

    def failing(self, *a, **k):
        calls["n"] += 1
        if calls["n"] == 4:
            raise RuntimeError("power cut")
        return real(self, *a, **k)

    monkeypatch.setattr(quality.Journal, "move", failing)
    with pytest.raises(RuntimeError):
        quality.merge_split_teams(db)
    assert table_digests(db) == before  # neither the change nor its journal


def test_the_dry_probe_reports_the_rows_it_would_change(db):
    db.con.execute("UPDATE matches SET away_team_id = home_team_id WHERE match_id = 1")
    before = table_digests(db)
    out = blocked_writes(db, quarantine, "quarantine")
    assert "matches removed 1" in out[0].detail and "rows:" in out[0].detail
    assert table_digests(db) == before


def test_repair_undo_command_lists_then_undoes_once(tmp_path, capsys):
    from fpl_agent.cli import main

    path = tmp_path / "f.duckdb"
    db = FootballDB(path)
    db.con.execute("INSERT INTO teams VALUES (1, 'A', 'a')")
    db.con.execute("INSERT INTO matches (match_id, home_team_id, away_team_id) VALUES (7, 1, 1)")
    quarantine(db)
    rid = _journal_id(db)
    db.close()
    assert main(["repair-undo", "--db", str(path)]) == 0
    assert f"{rid}  matches removed 1" in capsys.readouterr().out
    assert main(["repair-undo", "--db", str(path), rid]) == 0
    assert main(["repair-undo", "--db", str(path), rid]) == 1  # already undone: nothing written
    db = FootballDB(path)
    assert db.con.execute("SELECT count(*) FROM matches").fetchone()[0] == 1
    db.close()


def test_rollback_keeps_json_text_exactly():
    """A JSON column comes back as the text that was stored (spacing and escapes included)."""
    from fpl_agent.db.quality import rollback_repair

    db = FootballDB()
    raw = '{"second_name":  "Raya Martín", "x": [1, 2.50]}'
    db.con.execute("INSERT INTO fpl_players_gw (season, gw, snapshot_at, fpl_id, now_cost, raw) "
                   "VALUES (2026, 1, '2026-08-01 10:00:00.123456', 1, 7, ?)", [raw])  # fmt: skip
    before = table_digests(db)
    quarantine(db)
    rollback_repair(db, _journal_id(db))
    assert db.con.execute("SELECT raw::VARCHAR FROM fpl_players_gw").fetchone()[0] == raw
    assert {t: d for t, d in table_digests(db).items() if t != "repair_log"} == {
        t: d for t, d in before.items() if t != "repair_log"}  # fmt: skip
