"""Gate 0 G2/K3: automatic repairs are opt-in and off by default; a needed repair that is not
allowed blocks the run visibly (exit 3, recorded, alerted, nothing loaded or exported); an
applied repair is journalled and `repair-undo` reverses it; a repair step that raises is
recorded, never a silent stop; no workflow opts in."""

from datetime import date
from pathlib import Path

import pytest

from fpl_agent import notify, pipeline
from fpl_agent.cli import main
from fpl_agent.db.database import FootballDB
from fpl_agent.db.schema import TABLES
from fpl_agent.pipeline import (
    BAD_DATA,
    OK,
    BackfillOptions,
    UpdateOptions,
    run_backfill,
    run_update,
)
from tests.helpers import clients

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def alerts(monkeypatch):
    sent = []
    monkeypatch.setattr(notify, "send", lambda msg, *a, **k: sent.append(msg) or True)
    return sent


def split_club(db: FootballDB) -> None:
    """One club stored twice by older code: a merge repair is needed."""
    mk = db.team_id("fotmob", "1", "Milton Keynes Dons")
    db.con.execute("INSERT INTO teams VALUES (90, 'Peterboro', 'peterboro')")
    db.con.execute("INSERT INTO team_ids VALUES ('football-data', 'Peterboro', 90, 'Peterboro')")
    db.con.execute("INSERT INTO teams VALUES (91, 'Peterborough United', 'peterborough united')")
    db.con.execute("INSERT INTO team_ids VALUES ('fotmob', '8677', 91, 'Peterborough United')")
    db.match_id("football-data", "x", "ENG3", 2026, date(2026, 9, 12), mk, 90,
                home_goals=1, away_goals=1, finished=True)  # fmt: skip
    db.match_id("fotmob", 5, "ENG3", 2026, date(2026, 9, 12), mk, 91, matchday=6)


def data(db):
    skip = {"source_attempts", "repair_log"}
    return {
        t: db.con.execute(f"SELECT count(*), sum(hash(x))::VARCHAR FROM {t} x").fetchone()
        for t in TABLES
        if t not in skip
    }


def update(db, **kw):
    fpl, us, http = clients()
    opts = UpdateOptions(season=2026, football_data=("E0",), **kw)
    return run_update(db, opts, fpl=fpl, us=us, http=http)


def attempts(db):
    return db.query("SELECT source, ok, exit_code FROM source_attempts ORDER BY seq")


def test_repairs_are_off_by_default_and_no_workflow_opts_in():
    assert UpdateOptions().repairs is False and BackfillOptions().repairs is False
    flows = list((ROOT / ".github" / "workflows").glob("*.yml"))
    assert flows and not [f.name for f in flows if "--repairs" in f.read_text(encoding="utf-8")]


def test_a_needed_repair_blocks_before_loading_and_is_visible(alerts, tmp_path):
    db = FootballDB()
    split_club(db)
    before = data(db)
    res = update(db, export=tmp_path / "out")
    assert res.exit_code == BAD_DATA
    assert any(i.check == "repair_blocked" and i.severity == "error" for i in res.issues)
    assert data(db) == before  # nothing repaired, nothing loaded (no players, no deadlines)
    assert db.counts()["fpl_deadlines"] == 0 and db.counts()["repair_log"] == 0
    assert not (tmp_path / "out").exists()
    assert alerts and "exit 3" in alerts[0] and "repair_blocked" in alerts[0]
    got = attempts(db)
    assert got.source.tolist() == ["step:repair"] and (got.exit_code == BAD_DATA).all()


def test_a_backfill_is_blocked_the_same_way(alerts):
    db = FootballDB()
    split_club(db)
    before = data(db)
    res = run_backfill(db, BackfillOptions(sources=()))
    assert res.exit_code == BAD_DATA and data(db) == before
    assert db.query("SELECT DISTINCT job FROM source_attempts").job.tolist() == ["backfill"]


def test_a_needed_quarantine_blocks_the_export_and_keeps_the_rows(alerts, tmp_path):
    db = FootballDB()
    update(db)  # a clean first load
    db.con.execute("UPDATE fpl_players_gw SET now_cost = 5 WHERE fpl_id = 1")
    res = update(db, export=tmp_path / "out")
    assert res.exit_code == BAD_DATA and not (tmp_path / "out").exists()
    assert any(i.check == "quarantine_blocked" for i in res.issues)
    assert db.con.execute("SELECT count(*) FROM fpl_players_gw WHERE now_cost = 5").fetchone()[0]
    assert alerts and attempts(db).exit_code.iloc[-1] == BAD_DATA


def test_an_opted_in_repair_is_applied_and_journalled(alerts, tmp_path, capsys):
    path = tmp_path / "f.duckdb"
    db = FootballDB(path)
    split_club(db)
    res = update(db, repairs=True)
    assert res.exit_code == OK, res.message()
    assert any(i.check == "merged_teams" for i in res.issues)
    rid = db.con.execute(
        "SELECT max(repair_id) FROM repair_log WHERE step = 'merge-split-teams'"
    ).fetchone()[0]
    assert rid is not None
    before = data(db)
    db.close()
    # the update loaded new teams after the merge (one reuses the merged id): an undo now
    # would collide, so it is refused and writes nothing
    assert main(["repair-undo", "--db", str(path), rid]) == 1
    assert "not undone" in capsys.readouterr().out
    db = FootballDB(path)
    assert data(db) == before
    db.close()


def test_the_cli_lists_and_undoes_a_repair_exactly(tmp_path, capsys):
    from fpl_agent.db.quality import repair

    path = tmp_path / "f.duckdb"
    db = FootballDB(path)
    split_club(db)
    before = data(db)
    assert repair(db)
    ids = [r[0] for r in db.con.execute(
        "SELECT DISTINCT repair_id FROM repair_log ORDER BY 1 DESC").fetchall()]  # fmt: skip
    db.close()
    assert main(["repair-undo", "--db", str(path)]) == 0
    assert "merge-split-teams" in capsys.readouterr().out
    for rid in ids:
        assert main(["repair-undo", "--db", str(path), rid]) == 0
    assert main(["repair-undo", "--db", str(path), ids[0]]) == 0  # a repeat changes nothing
    assert "already undone" in capsys.readouterr().out
    db = FootballDB(path)
    assert data(db) == before
    db.close()


def test_repair_undo_refuses_an_unknown_id_and_an_older_schema(tmp_path, capsys):
    path = tmp_path / "f.duckdb"
    FootballDB(path).close()
    assert main(["repair-undo", "--db", str(path), "nope:1"]) == 1
    assert "no journalled change" in capsys.readouterr().out
    import duckdb

    old = tmp_path / "old.duckdb"
    con = duckdb.connect(str(old))
    con.execute("CREATE TABLE teams (team_id INTEGER)")
    con.close()
    assert main(["repair-undo", "--db", str(old), "x:1"]) == 1
    assert "lacks declared tables" in capsys.readouterr().out
    con = duckdb.connect(str(old), read_only=True)
    assert con.execute("SELECT count(*) FROM duckdb_tables()").fetchone()[0] == 1  # unchanged
    con.close()


@pytest.mark.parametrize("step", ["repair", "quarantine", "run_checks"])
def test_a_step_that_raises_is_recorded_and_alerted(alerts, monkeypatch, tmp_path, step):
    def boom(*a, **k):
        raise RuntimeError("disk full")

    monkeypatch.setattr(pipeline, step, boom)
    db = FootballDB()
    res = update(db, export=tmp_path / "out")
    assert res.exit_code == BAD_DATA and not (tmp_path / "out").exists()
    name = {"repair": "step:repair", "quarantine": "step:quarantine",
            "run_checks": "step:checks"}[step]  # fmt: skip
    got = attempts(db).set_index("source")
    assert not got.loc[name, "ok"] and got.loc[name, "exit_code"] == BAD_DATA
    assert alerts and "disk full" in alerts[0]
    if step == "repair":
        assert db.counts()["players"] == 0  # nothing loaded after a failed repair step
