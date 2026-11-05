"""Additive schema changes: an older DuckDB file upgrades in place, an older parquet export
still restores, migrations run once, and the new CLI commands start on any database."""

import json

import duckdb
import pandas as pd
import pytest

from fpl_agent.cli import main
from fpl_agent.db.database import FootballDB
from fpl_agent.db.schema import MIGRATIONS, TABLES

OLD_PROJECTIONS = """run_id VARCHAR, model VARCHAR, created_at TIMESTAMP, season INTEGER,
    gw INTEGER, player_id INTEGER, xpts DOUBLE, detail JSON"""
OLD_REVIEWS = "season INTEGER, gw INTEGER, created_at TIMESTAMP, kind VARCHAR, payload JSON"


def _old_file(path):
    con = duckdb.connect(str(path))
    con.execute(f"CREATE TABLE projections ({OLD_PROJECTIONS})")
    con.execute(f"CREATE TABLE reviews ({OLD_REVIEWS})")
    con.execute("INSERT INTO projections VALUES ('r1', 'sim-v1', TIMESTAMP '2026-10-20 10:00', "
                "2026, 9, 7, 3.5, '{}')")  # fmt: skip
    con.close()


def test_an_older_duckdb_file_upgrades_in_place(tmp_path):
    path = tmp_path / "old.duckdb"
    _old_file(path)
    db = FootballDB(path)
    assert "variant" in db.columns("projections")
    assert db.query("SELECT run_id, xpts, variant FROM projections").values.tolist() == [
        ["r1", 3.5, None]
    ]
    for t in ("forecast_runs", "learning_state", "learning_updates", "fpl_events"):
        assert db.counts()[t] == 0
    assert {r[0] for r in db.con.execute("SELECT name FROM schema_migrations").fetchall()} == {
        n for n, _ in MIGRATIONS
    }
    db.close()
    again = FootballDB(path)  # a second open applies nothing twice
    assert again.migrate() == []
    assert again.counts()["schema_migrations"] == len(MIGRATIONS)


def test_an_older_parquet_export_still_restores(tmp_path):
    con = duckdb.connect()
    con.execute(f"CREATE TABLE projections ({OLD_PROJECTIONS})")
    con.execute("INSERT INTO projections VALUES ('r1', 'sim-v1', now(), 2026, 9, 7, 3.5, '{}')")
    con.execute(f"COPY projections TO '{tmp_path / 'projections.parquet'}' (FORMAT parquet)")
    db = FootballDB()
    db.import_parquet(tmp_path)
    got = db.query("SELECT run_id, variant FROM projections")
    assert got.values.tolist() == [["r1", None]]


def test_every_table_round_trips_through_parquet(tmp_path):
    db = FootballDB()
    db.upsert("fpl_events", pd.DataFrame([{"season": 2026, "gw": 1, "finished": True}]))
    db.append("learning_state", pd.DataFrame([{"version": 1, "status": "current",
                                               "config": json.dumps({})}]))  # fmt: skip
    counts = db.export_parquet(tmp_path)
    assert set(counts) == set(TABLES)
    fresh = FootballDB()
    fresh.import_parquet(tmp_path)
    assert fresh.counts()["fpl_events"] == 1 and fresh.counts()["learning_state"] == 1


@pytest.mark.parametrize("argv", [
    ["features"], ["learn", "--history"], ["evaluate", "--gw", "3"],
    ["learn", "--gw", "3"], ["learn", "--rollback"],
])  # fmt: skip
def test_new_commands_run_on_an_empty_database(tmp_path, argv, capsys):
    assert main([*argv, "--db", str(tmp_path / "x.duckdb")]) == 0
    assert capsys.readouterr().out.strip()


def test_walk_forward_cli_reports_missing_history(tmp_path, capsys):
    assert main(["walk-forward", "--db", str(tmp_path / "x.duckdb"), "--from-gw", "1",
                 "--to-gw", "2"]) == 2  # fmt: skip
    assert "not enough history" in capsys.readouterr().out
