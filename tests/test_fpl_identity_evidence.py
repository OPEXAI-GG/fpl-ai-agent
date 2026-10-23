"""R1 identity evidence: people are linked by FPL's stable `code` (never by name), contradictory
evidence blocks the relabel, the migration journal is verified field by field and survives
export/import. Helpers come from test_fpl_identity."""

import pytest

from fpl_agent.db import fpl_identity as fi
from fpl_agent.db.database import FootballDB
from fpl_agent.db.quality import run_checks
from tests.test_fpl_identity import (
    B26,
    B27,
    F26,
    F27,
    ID_TABLES,
    boot,
    digest,
    element,
    ingest,
    live,
    to_legacy,
)


@pytest.fixture
def db():
    d = FootballDB()
    ingest(d, 2026, B26, F26, live(1, 5))
    return d


def codes(db):
    return dict(db.con.execute("SELECT code, player_id FROM player_codes").fetchall())


def p27(db):
    return dict(
        db.con.execute(
            "SELECT source_id, player_id FROM player_ids WHERE source = 'fpl-2027'"
        ).fetchall()
    )


def flag(db, key):
    row = db.con.execute(
        "SELECT detail FROM ingest_state WHERE source = 'link' AND key = ?", [key]
    ).fetchone()
    return row[0] if row else None


def refused(db, problem):
    before = digest(db)
    with pytest.raises(fi.MigrationRefused) as e:
        fi.migrate(db.con)
    assert problem in str(e.value)
    assert digest(db) == before  # nothing written, journal stays empty


# ---- identity by stable code, never by name ----------------------------------------------


def test_a_namesake_with_a_different_code_is_a_different_person(db):
    ingest(
        db,
        2027,
        boot({1: "Arsenal", 2: "Chelsea"}, [element(5, "Alice", "Smith", 1, code=999)]),
        F27,
    )
    assert p27(db)["5"] != codes(db)["111"] and codes(db)["999"] == p27(db)["5"]
    # two different people sharing a name is exactly what the quality warning is for
    assert [i.severity for i in run_checks(db) if i.check == "duplicate_player_names"] == ["warn"]


def test_same_code_links_across_id_club_position_and_name_changes(db):
    ingest(db, 2027, B27, F27)
    c = codes(db)
    assert p27(db)["9"] == c["111"] and p27(db)["2"] == c["222"]
    assert p27(db)["1"] == c["333"] and c["333"] not in (c["111"], c["222"])


def test_a_record_without_a_code_stays_separate_and_is_flagged(db):
    ingest(db, 2027, boot({1: "Arsenal", 2: "Chelsea"}, [element(9, "Alice", "Smith", 1)]), F27)
    assert p27(db)["9"] != codes(db)["111"]
    assert "no stable code" in flag(db, "fpl-2027:9")
    assert len(codes(db)) == 2  # nothing recorded for the record without a code


def test_a_code_already_used_by_another_id_this_season_is_not_merged(db):
    twice = boot(
        {1: "Arsenal", 2: "Chelsea"},
        [element(9, "Alice", "Smith", 1, code=111), element(10, "Alice", "Smith", 1, code=111)],
    )
    ingest(db, 2027, twice, F27)
    assert p27(db)["9"] == codes(db)["111"] and p27(db)["10"] != p27(db)["9"]
    assert "already has another FPL id" in flag(db, "fpl-2027:10")


def test_codes_are_seeded_from_stored_snapshots_and_conflicts_are_not_trusted(db):
    db.con.execute("DELETE FROM player_codes")
    assert fi.FplIdentity(db, 2027).seed_codes() == {"recorded": 2, "flagged": 0}
    # a snapshot that attributes code 111 to a second player is not trusted for either
    db.con.execute(
        "INSERT INTO fpl_players_gw (season, gw, snapshot_at, fpl_id, player_id, raw) "
        "SELECT 2025, 1, snapshot_at, 77, 2, json_object('code', 111) FROM fpl_players_gw LIMIT 1"
    )
    db.con.execute("DELETE FROM player_codes")
    out = fi.FplIdentity(db, 2027).seed_codes()
    assert out["flagged"] >= 1 and "111" not in codes(db)
    assert "players" in flag(db, "fpl-code:111")


def attribute_111_to_player_2(db):
    """A historical snapshot found after 111 was recorded for player 1 says it is player 2's."""
    db.con.execute(
        "INSERT INTO fpl_players_gw (season, gw, snapshot_at, fpl_id, player_id, raw) "
        "SELECT 2025, 1, snapshot_at, 77, 2, json_object('code', 111) FROM fpl_players_gw LIMIT 1"
    )
    return fi.FplIdentity(db, 2027).seed_codes()


def alice_2027(db, code=111):
    ingest(db, 2027, boot({1: "Arsenal", 2: "Chelsea"}, [element(9, "Alice", "Smith", 1, code=code)]),
           F27)  # fmt: skip
    return p27(db)["9"]


def test_a_code_disputed_after_it_was_recorded_does_not_drive_a_link(db):
    alice = codes(db)["111"]
    assert attribute_111_to_player_2(db)["flagged"] >= 1
    assert fi.FplIdentity(db, 2027).player_id(9, "Different", "MID", code=111) != alice
    assert codes(db)["111"] == alice  # the recorded evidence is kept, not deleted
    assert "is disputed; kept separate" in flag(db, "fpl-2027:9")


def test_a_player_seen_with_several_codes_is_not_a_link_target(db):
    db.con.execute(
        "INSERT INTO fpl_players_gw (season, gw, snapshot_at, fpl_id, player_id, raw) "
        "SELECT 2025, 1, snapshot_at, 77, 1, json_object('code', 555) FROM fpl_players_gw LIMIT 1"
    )
    fi.FplIdentity(db, 2027).seed_codes()
    assert "codes" in flag(db, "fpl-player:1") and "555" not in codes(db)
    assert alice_2027(db) != codes(db)["111"]
    assert "player 1 is disputed" in flag(db, "fpl-2027:9")


def test_an_existing_mapping_reporting_another_code_disputes_the_player(db):
    alice = codes(db)["111"]
    changed = [element(1, "Alice", "Smith", 1, code=444), element(2, "Bob", "Jones", 2, code=222)]
    ingest(db, 2026, boot({1: "Arsenal", 2: "Chelsea"}, changed), F26, gw=2)
    assert "444" in flag(db, "fpl-player:1") and "444" not in codes(db)
    assert codes(db)["111"] == alice  # kept as evidence
    assert alice_2027(db) != alice  # neither code links to the disputed player


def test_later_ingestion_and_reseeding_cannot_reactivate_a_disputed_code(db):
    alice = codes(db)["111"]
    attribute_111_to_player_2(db)
    first = flag(db, "fpl-code:111")
    db.con.execute("DELETE FROM fpl_players_gw WHERE season = 2025")  # the evidence vanishes
    ingest(db, 2026, B26, F26, live(1, 5), gw=2)
    assert fi.FplIdentity(db, 2027).seed_codes()["flagged"] >= 1
    assert flag(db, "fpl-code:111").startswith(first)  # the original reason is kept
    assert alice_2027(db) != alice


def test_reloading_a_season_records_each_code_once(db):
    ingest(db, 2026, B26, F26, gw=2)
    assert len(codes(db)) == 2


def test_a_recorded_code_pointing_elsewhere_is_flagged_not_rewritten(db):
    other = codes(db)["222"]
    db.con.execute("UPDATE player_codes SET player_id = ? WHERE code = '111'", [other])
    ingest(db, 2026, B26, F26, gw=2)
    assert codes(db)["111"] == other and "recorded for player" in flag(db, "fpl-code:111")


# ---- contradictory evidence is refused, not filtered away --------------------------------


def two_snapshots(db):
    ingest(db, 2026, B26, F26, live(1, 5), gw=2)
    to_legacy(db)


def test_a_conflicting_points_row_blocks_the_migration_atomically(db):
    to_legacy(db)
    db.con.execute("UPDATE fpl_points_gw SET player_id = 2 WHERE fpl_id = 1")
    refused(db, "player_ids 1: conflicting evidence")
    with pytest.raises(fi.IdentityError):
        fi.source_for(db.con, "player", 2026, 1)
    with pytest.raises(fi.IdentityError):
        fi.season_map(db.con, "player", 2026)
    assert fi.source_for(db.con, "player", 2027, 1) == "fpl-2027"  # unrelated season
    assert fi.source_for(db.con, "player", 2026, 2) == "fpl"  # other ids still resolve


def test_mixed_matching_and_conflicting_snapshot_rows_block_the_migration(db):
    two_snapshots(db)
    db.con.execute("UPDATE fpl_players_gw SET player_id = 2 WHERE fpl_id = 1 AND gw = 2")
    assert fi.legacy_seasons(db.con, "player")["1"][1] == {2026}  # matching rows still prove it
    refused(db, "player_ids 1: conflicting evidence")


def test_a_conflicting_club_reference_blocks_the_migration(db):
    two_snapshots(db)
    other = db.con.execute("SELECT team_id FROM team_ids WHERE source_id = '2'").fetchone()[0]
    db.con.execute("UPDATE fpl_players_gw SET team_id = ? WHERE fpl_id = 1 AND gw = 2", [other])
    refused(db, "team_ids 1: conflicting evidence")


def test_legitimate_reuse_with_its_own_label_is_not_a_conflict(db):
    to_legacy(db)
    ingest(db, 2027, B27, F27)  # id 1 now names Cara in 2027, under fpl-2027
    assert not fi.legacy_conflicts(db.con, "player")
    assert fi.migrate(db.con)["status"] == "migrated"


# ---- the journal is verified field by field, and persisted -------------------------------


def test_name_only_change_to_a_migrated_row_refuses_noop_and_rollback(db):
    to_legacy(db)
    fi.migrate(db.con)
    for new in ("Corrected later", None):
        db.con.execute(
            "UPDATE player_ids SET source_name = ? WHERE source = 'fpl-2026' AND source_id = '1'",
            [new],
        )
        changed = digest(db)
        with pytest.raises(fi.MigrationRefused):
            fi.migrate(db.con)
        with pytest.raises(fi.MigrationRefused):
            fi.rollback(db.con)
        assert digest(db) == changed  # refusal wrote nothing


def test_a_club_name_change_refuses_rollback(db):
    to_legacy(db)
    fi.migrate(db.con)
    db.con.execute(
        "UPDATE team_ids SET source_name = 'Renamed' WHERE source = 'fpl-2026' AND source_id = '1'"
    )
    with pytest.raises(fi.MigrationRefused):
        fi.rollback(db.con)


def test_canonical_only_change_refuses_noop(db):
    to_legacy(db)
    fi.migrate(db.con)
    db.con.execute(
        "UPDATE player_ids SET player_id = 2 WHERE source = 'fpl-2026' AND source_id = '1'"
    )
    with pytest.raises(fi.MigrationRefused):
        fi.migrate(db.con)


def test_a_null_name_is_compared_null_safely(db):
    to_legacy(db)
    db.con.execute("UPDATE player_ids SET source_name = NULL WHERE source_id = '1'")
    fi.migrate(db.con)
    assert fi.migrate(db.con)["status"] == "noop-verified"
    assert fi.rollback(db.con)["status"] == "rolled-back"
    assert db.con.execute(
        "SELECT source_name FROM player_ids WHERE source = 'fpl' AND source_id = '1'"
    ).fetchone() == (None,)


def test_export_import_keeps_the_journal_then_noop_and_rollback_work(db, tmp_path):
    to_legacy(db)
    fi.migrate(db.con)
    counts = db.export_parquet(tmp_path / "export")
    assert counts["migration_log"] == 5 and counts["player_codes"] == len(codes(db))
    restored = FootballDB()
    assert restored.import_parquet(tmp_path / "export", require_manifest=True) > 0
    keep = [fi.JOURNAL, "player_codes", *ID_TABLES]
    assert digest(restored, keep) == digest(db, keep)
    assert restored.import_parquet(tmp_path / "export", require_manifest=True) == 0  # duplicate
    assert digest(restored, keep) == digest(db, keep)
    assert fi.migrate(restored.con) == {"status": "noop-verified", "rows": 5}
    assert fi.rollback(restored.con)["status"] == "rolled-back"
    labels = restored.con.execute("SELECT DISTINCT source FROM player_ids").fetchall()
    assert labels == [("fpl",)]
    assert fi.migrate(restored.con)["status"] == "migrated"


def test_a_corrupt_import_loads_nothing(db, tmp_path):
    to_legacy(db)
    fi.migrate(db.con)
    db.export_parquet(tmp_path / "export")
    (tmp_path / "export" / "migration_log.parquet").write_bytes(b"not parquet")
    target = FootballDB()
    with pytest.raises(Exception, match="migration_log"):
        target.import_parquet(tmp_path / "export", require_manifest=True)
    assert all(n == 0 for n in target.counts().values())


def test_an_import_that_fails_part_way_leaves_the_target_unchanged(db, tmp_path, monkeypatch):
    to_legacy(db)
    fi.migrate(db.con)
    db.export_parquet(tmp_path / "export")
    target = FootballDB()
    before = digest(target)
    real = target.con

    class Failing:
        """Fails the journal's INSERT, after the earlier tables were already written."""

        def execute(self, sql, *a):
            if "INTO migration_log" in sql:
                raise RuntimeError("interrupted")
            return real.execute(sql, *a)

        def __getattr__(self, name):
            return getattr(real, name)

    monkeypatch.setattr(target, "con", Failing())
    with pytest.raises(RuntimeError):
        target.import_parquet(tmp_path / "export", require_manifest=True)
    monkeypatch.undo()
    assert digest(target) == before
    assert not target.con.execute("SELECT 1 FROM ingest_state WHERE source = 'import'").fetchall()
