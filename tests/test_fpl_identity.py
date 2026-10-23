"""Season-scoped live FPL identity: resolver, ingestion across seasons, and the reversible
relabelling of legacy `fpl` rows (always on an in-memory or disposable database)."""

import pytest

from fpl_agent.db import fpl_identity as fi
from fpl_agent.db import ingest_fpl
from fpl_agent.db.database import FootballDB
from fpl_agent.db.quality import run_checks
from fpl_agent.review import _cutoff

ID_TABLES = ("player_ids", "team_ids", "match_ids")


def element(eid, first, second, team, kind=3, code=None):
    return {
        "id": eid, "code": code, "first_name": first, "second_name": second, "element_type": kind,
        "team": team, "now_cost": 60, "selected_by_percent": "1.0", "status": "a",
        "total_points": 0, "minutes": 0,
    }  # fmt: skip


def boot(teams, elements):
    return {"teams": [{"id": i, "name": n} for i, n in teams.items()], "elements": elements}


def fixture_row(fid, home, away, event, kickoff, score=None):
    return {
        "id": fid, "kickoff_time": kickoff, "team_h": home, "team_a": away, "event": event,
        "team_h_score": score[0] if score else None, "team_a_score": score[1] if score else None,
        "finished": score is not None,
    }  # fmt: skip


def live(eid, fixture, minutes=90, points=6):
    return {"elements": [{"id": eid, "stats": {"minutes": minutes, "total_points": points},
                          "explain": [{"fixture": fixture, "stats": [
                              {"identifier": "minutes", "points": 2, "value": minutes}]}]}]}  # fmt: skip


# 2026: ids 1 (Alice Smith, Arsenal) and 2 (Bob Jones, Chelsea); fixture 5 Arsenal v Chelsea 2-1
B26 = boot(
    {1: "Arsenal", 2: "Chelsea"},
    [element(1, "Alice", "Smith", 1, code=111), element(2, "Bob", "Jones", 2, code=222)],
)
F26 = [fixture_row(5, 1, 2, 1, "2026-08-15T14:00:00Z", (2, 1))]
# 2027 reuses 1 and 5 for different things: id 1 is Chelsea now, element 1 is Cara Diaz, Alice
# (code 111) is element 9 at another club in another position, Bob keeps id 2 under a new
# spelling, and fixture 5 is Chelsea v Arsenal.
B27 = boot(
    {1: "Chelsea", 2: "Arsenal"},
    [
        element(1, "Cara", "Diaz", 1, code=333),
        element(9, "Alice", "Smith-Jones", 1, kind=4, code=111),
        element(2, "Robert", "Jones", 1, code=222),
    ],
)
F27 = [fixture_row(5, 1, 2, 1, "2027-08-14T14:00:00Z")]


def digest(db, tables=None):
    """Order-free multiset of every table (or the named ones)."""
    out = {}
    names = tables or [r[0] for r in db.con.execute("SHOW TABLES").fetchall()]
    for t in names:
        out[t] = sorted(map(repr, db.con.execute(f"SELECT * FROM {t}").fetchall()))
    return out


def ingest(db, season, b, fx, live_payload=None, gw=1):
    ingest_fpl.load_bootstrap(db, b, season, gw)
    ingest_fpl.load_fixtures(db, fx, b, season)
    if live_payload:
        ingest_fpl.load_live(db, live_payload, season, gw)


def to_legacy(db, season=2026):
    """Put a database into the state before this change: identity rows under `fpl`."""
    label = fi.season_source(season)
    for t in ID_TABLES:
        db.con.execute(f"UPDATE {t} SET source = 'fpl' WHERE source = ?", [label])


@pytest.fixture
def db():
    d = FootballDB()
    ingest(d, 2026, B26, F26, live(1, 5))
    return d


def test_new_ingestion_uses_season_labels_and_keeps_provenance(db):
    for t in ID_TABLES:
        assert {r[0] for r in db.con.execute(f"SELECT DISTINCT source FROM {t}").fetchall()} == {
            "fpl-2026"
        }
    assert db.con.execute("SELECT DISTINCT source FROM matches").fetchall() == [("fpl",)]
    assert {r[0] for r in db.con.execute("SELECT source FROM ingest_log").fetchall()} == {"fpl"}


def test_reused_ids_are_different_entities_and_same_player_keeps_one_canonical(db):
    before = db.con.execute("SELECT * FROM matches").fetchall()
    ingest(db, 2027, B27, F27, live(1, 5, 60, 2))
    players = dict(db.con.execute("SELECT source_id, player_id FROM player_ids "
                                  "WHERE source = 'fpl-2026'").fetchall())  # fmt: skip
    p27 = dict(db.con.execute("SELECT source_id, player_id FROM player_ids "
                              "WHERE source = 'fpl-2027'").fetchall())  # fmt: skip
    # Alice: new element id, club, position and name, same code -> same canonical player
    assert p27["9"] == players["1"]
    assert p27["2"] == players["2"]  # Bob: renamed, same code
    assert p27["1"] not in players.values()  # id 1 now names Cara
    teams = dict(db.con.execute("SELECT source_id, team_id FROM team_ids "
                                "WHERE source = 'fpl-2026'").fetchall())  # fmt: skip
    t27 = dict(db.con.execute("SELECT source_id, team_id FROM team_ids "
                              "WHERE source = 'fpl-2027'").fetchall())  # fmt: skip
    assert t27["1"] == teams["2"] and t27["2"] == teams["1"]  # clubs follow their names
    ms = db.con.execute("SELECT season, home_team_id, home_goals, away_goals FROM matches "
                        "ORDER BY season").fetchall()  # fmt: skip
    assert len(ms) == 2 and ms[0][2:] == (2, 1) and ms[1][2:] == (None, None)
    assert db.con.execute("SELECT * FROM matches WHERE season = 2026").fetchall() == before
    pts = db.con.execute("SELECT season, fixture_id, player_id, minutes FROM fpl_points_gw "
                         "ORDER BY season").fetchall()  # fmt: skip
    assert pts[0][0] == 2026 and pts[0][2] == players["1"] and pts[0][3] == 90
    assert pts[1][0] == 2027 and pts[1][2] == p27["1"] and pts[1][3] == 60  # Cara, not Alice
    assert not [i for i in run_checks(db) if i.check == "duplicate_player_names"]


def test_a_missing_season_mapping_is_not_read_from_another_season(db):
    to_legacy(db)
    assert fi.source_for(db.con, "player", 2026, 1) == "fpl"
    assert fi.source_for(db.con, "player", 2027, 1) == "fpl-2027"
    assert fi.season_map(db.con, "player", 2027) == {}
    assert fi.season_map(db.con, "player", 2026).keys() == {1, 2}


def test_legacy_rows_are_reused_by_their_own_season_without_new_rows(db):
    to_legacy(db)
    before = digest(db, ID_TABLES)
    ingest(db, 2026, B26, F26, live(1, 5), gw=2)
    assert digest(db, ID_TABLES) == before


def test_ingesting_a_new_season_before_migration_then_migrating(db):
    to_legacy(db)
    ingest(db, 2027, B27, F27, live(1, 5, 60, 2))
    base = digest(db, [t for t in digest(db) if t not in (*ID_TABLES, fi.JOURNAL)])
    out = fi.migrate(db.con)
    assert out["status"] == "migrated" and out["counts"] == {
        "player_ids": 2, "team_ids": 2, "match_ids": 1}  # fmt: skip
    assert digest(db, list(base)) == base
    assert not db.con.execute("SELECT 1 FROM player_ids WHERE source = 'fpl'").fetchall()
    # Alice's legacy id 1 kept its 2026 dependents even though id 1 exists in 2027 as Cara
    assert fi.source_for(db.con, "player", 2026, 1) == "fpl-2026"


def test_same_raw_id_and_canonical_in_two_seasons_is_not_ambiguous_once_labelled(db):
    to_legacy(db)
    same = boot({1: "Arsenal", 2: "Chelsea"}, [element(1, "Alice", "Smith", 1, code=111)])
    ingest(db, 2027, same, F27, live(1, 7))
    assert fi.legacy_seasons(db.con, "player")["1"][1] == {2026}
    assert fi.migrate(db.con)["status"] == "migrated"
    canon = db.con.execute("SELECT player_id FROM player_ids WHERE source_id = '1' "
                           "ORDER BY source").fetchall()  # fmt: skip
    assert len(canon) == 2 and canon[0] == canon[1]


def test_migration_is_exact_and_idempotent(db):
    to_legacy(db)
    before = digest(db)
    names = db.con.execute("SELECT source_id, player_id, source_name FROM player_ids").fetchall()
    out = fi.migrate(db.con)
    assert out["status"] == "migrated" and out["rows"] == 5
    after = digest(db)
    assert {k: v for k, v in after.items() if k not in (*ID_TABLES, fi.JOURNAL)} == {
        k: v for k, v in before.items() if k not in (*ID_TABLES, fi.JOURNAL)}  # fmt: skip
    assert db.con.execute("SELECT source_id, player_id, source_name FROM player_ids "
                          "WHERE source = 'fpl-2026'").fetchall() == names  # fmt: skip
    again = fi.migrate(db.con)
    assert again == {"status": "noop-verified", "rows": 5}
    assert digest(db) == after


def test_nothing_to_migrate_on_a_new_database(db):
    assert fi.migrate(db.con) == {"status": "nothing-to-migrate", "rows": 0}
    assert digest(db, [fi.JOURNAL]) == {fi.JOURNAL: []}  # the table exists, empty


def test_rollback_restores_the_exact_legacy_state(db):
    to_legacy(db)
    before = digest(db)
    fi.migrate(db.con)
    assert fi.rollback(db.con)["status"] == "rolled-back"
    after = digest(db)
    assert {k: v for k, v in after.items() if k != fi.JOURNAL} == {
        k: v for k, v in before.items() if k != fi.JOURNAL}  # fmt: skip
    assert db.con.execute(f"SELECT count(*) FROM {fi.JOURNAL} WHERE rolled_back_at IS NULL"
                          ).fetchone()[0] == 0  # fmt: skip
    assert fi.rollback(db.con)["status"] == "nothing-to-roll-back"
    assert fi.migrate(db.con)["status"] == "migrated"  # and can run again


def _refused(db, problem):
    before = digest(db)
    with pytest.raises(fi.MigrationRefused) as e:
        fi.migrate(db.con)
    assert problem in str(e.value)
    assert digest(db) == before  # nothing written, not even the journal


def test_refuses_a_legacy_row_with_no_proof(db):
    to_legacy(db)
    db.con.execute("INSERT INTO player_ids VALUES ('fpl', '99', 1, 'Ghost')")
    _refused(db, "player_ids 99: no proof of season")


def test_refuses_a_row_proven_for_two_seasons(db):
    to_legacy(db)
    db.con.execute(
        "INSERT INTO fpl_players_gw (season, gw, snapshot_at, fpl_id, player_id, raw) "
        "SELECT 2027, 1, snapshot_at, fpl_id, player_id, raw FROM fpl_players_gw WHERE fpl_id = 1"
    )
    _refused(db, "player_ids 1: proven for [2026, 2027]")


def test_refuses_when_the_target_row_exists_identical_or_conflicting(db):
    to_legacy(db)
    row = db.con.execute("SELECT player_id, source_name FROM player_ids WHERE source_id = '2'"
                         ).fetchone()  # fmt: skip
    db.con.execute("INSERT INTO player_ids VALUES ('fpl-2026', '2', ?, ?)", list(row))
    _refused(db, "player_ids 2: no proof of season (fpl-2026 row exists for this id)")
    db.con.execute("UPDATE player_ids SET player_id = 1 WHERE source = 'fpl-2026'")
    _refused(db, "player_ids 2: no proof of season (fpl-2026 row exists for this id)")


def test_refuses_a_canonical_reference_that_does_not_exist(db):
    to_legacy(db)
    db.con.execute("UPDATE team_ids SET team_id = 999 WHERE source_id = '1'")
    _refused(db, "team_ids 1")


def test_raw_id_alone_is_not_proof(db):
    to_legacy(db)
    # same raw id, but the dependent rows point at another canonical player: no proof
    db.con.execute("UPDATE fpl_players_gw SET player_id = 12345 WHERE fpl_id = 1")
    db.con.execute("UPDATE fpl_points_gw SET player_id = 12345 WHERE fpl_id = 1")
    _refused(db, "player_ids 1: no proof of season")


def test_interrupted_migration_and_rollback_leave_nothing(db, monkeypatch):
    to_legacy(db)
    before = digest(db)

    def boom(*_a):
        raise RuntimeError("interrupted")

    monkeypatch.setattr(fi, "_verify", boom)
    with pytest.raises(RuntimeError):
        fi.migrate(db.con)
    assert digest(db) == before
    monkeypatch.undo()
    fi.migrate(db.con)
    done = digest(db)
    monkeypatch.setattr(fi, "_present", lambda c, e, w: [] if w == "new" else ["x"])
    with pytest.raises(fi.IdentityError):
        fi.rollback(db.con)
    monkeypatch.undo()
    assert digest(db) == done


def test_rollback_refuses_when_a_migrated_row_changed_or_legacy_reappeared(db):
    to_legacy(db)
    fi.migrate(db.con)
    db.con.execute("UPDATE player_ids SET player_id = 2 WHERE source = 'fpl-2026' "
                   "AND source_id = '1'")  # fmt: skip
    with pytest.raises(fi.MigrationRefused):
        fi.rollback(db.con)
    db.con.execute("UPDATE player_ids SET player_id = 1 WHERE source = 'fpl-2026' "
                   "AND source_id = '1'")  # fmt: skip
    db.con.execute("INSERT INTO player_ids VALUES ('fpl', '1', 1, 'x')")
    with pytest.raises(fi.MigrationRefused):
        fi.rollback(db.con)
    with pytest.raises(fi.MigrationRefused):  # and legacy rows after a migration stop a re-run
        fi.migrate(db.con)


def test_noop_stops_when_a_journalled_row_is_gone(db):
    to_legacy(db)
    fi.migrate(db.con)
    db.con.execute("DELETE FROM team_ids WHERE source = 'fpl-2026' AND source_id = '1'")
    with pytest.raises(fi.MigrationRefused):
        fi.migrate(db.con)


def test_other_sources_are_reported_and_untouched(db):
    to_legacy(db)
    db.con.execute("INSERT INTO player_ids VALUES ('understat', '7', 1, 'A'), "
                   "('vaastav-2025', '3', 2, 'B')")  # fmt: skip
    assert fi.preflight(db.con).other_sources["player_ids"] == {"understat": 1, "vaastav-2025": 1}
    fi.migrate(db.con)
    assert db.con.execute("SELECT count(*) FROM player_ids WHERE source IN "
                          "('understat', 'vaastav-2025')").fetchone()[0] == 2  # fmt: skip


def test_ambiguous_legacy_row_is_an_error_for_the_resolver(db):
    to_legacy(db)
    db.con.execute("INSERT INTO fpl_players_gw (season, gw, snapshot_at, fpl_id, player_id, raw) "
                   "SELECT 2027, 1, snapshot_at, fpl_id, player_id, raw FROM fpl_players_gw "
                   "WHERE fpl_id = 1")  # fmt: skip
    for season in (2026, 2027):  # the ambiguity concerns these seasons
        with pytest.raises(fi.IdentityError):
            fi.source_for(db.con, "player", season, 1)
        with pytest.raises(fi.IdentityError):
            fi.season_map(db.con, "player", season)
    # an unrelated season is not blocked by it, and other ids still resolve
    assert fi.source_for(db.con, "player", 2028, 1) == "fpl-2028"
    assert fi.source_for(db.con, "player", 2026, 2) == "fpl"
    assert fi.season_map(db.con, "player", 2028) == {}


def test_readers_give_the_same_answers_before_and_after_migration(db):
    ingest(db, 2026, B26, F26, live(1, 5), gw=2)
    to_legacy(db)

    def readers():
        pts = db.con.execute("SELECT * FROM fpl_points_gw ORDER BY gw").fetchall()
        return _cutoff(db, 2026, 1), fi.season_map(db.con, "player", 2026), pts

    before = readers()
    assert before[0] is not None
    fi.migrate(db.con)
    assert readers() == before
    ingest_fpl.load_live(db, live(1, 5), 2026, 2)  # live ingestion keeps working afterwards
    key = "SELECT season, gw, fpl_id, player_id, fixture_id, minutes FROM fpl_points_gw ORDER BY gw"
    assert db.con.execute(key).fetchall() == [r[:6] for r in before[2]]
