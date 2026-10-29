"""Gate 0 provenance: every declared table is exported, official deadlines, source attempts and
odds observations are recorded as they are seen, and every automatic repair is journalled so it
can be undone exactly."""

from copy import deepcopy
from datetime import date, datetime

import pytest

from fpl_agent import notify, pipeline
from fpl_agent.db import ingest_football_data, ingest_fpl
from fpl_agent.db.database import FootballDB, verify_export
from fpl_agent.db.gameweeks import official_deadline
from fpl_agent.db.quality import Issue, quarantine, repair, rollback_repair
from fpl_agent.db.schema import TABLES
from fpl_agent.health import HealthReport, SourceResult
from fpl_agent.pipeline import BAD_DATA, CRITICAL_SOURCE_FAILED, OK, UpdateOptions, run_update
from tests.helpers import FIX, clients, fixture

PROVENANCE = {"fpl_deadlines", "source_attempts", "match_odds_log"}
OPTS = UpdateOptions(season=2026, football_data=("E0", "E1"))


@pytest.fixture
def alerts(monkeypatch):
    sent = []
    monkeypatch.setattr(notify, "send", lambda msg, *a, **k: sent.append(msg) or True)
    return sent


def run(overrides=None, opts=OPTS, db=None):
    db = db or FootballDB()
    fpl, us, http = clients(overrides)
    return db, run_update(db, opts, fpl=fpl, us=us, http=http)


def digests(db):
    return {
        t: db.con.execute(f"SELECT count(*), sum(hash(x))::VARCHAR FROM {t} x").fetchone()
        for t in TABLES
        if t != "repair_log"
    }


def test_schema_declares_the_34_tables_and_an_export_carries_every_one(tmp_path):
    assert len(TABLES) == 34 and PROVENANCE | {"repair_log", "migration_log"} <= set(TABLES)
    db = FootballDB()
    counts = db.export_parquet(tmp_path / "out")
    assert set(counts) == set(TABLES) and counts["fpl_deadlines"] == 0  # empty tables too
    assert verify_export(tmp_path / "out") == []
    assert {p.stem for p in (tmp_path / "out").glob("*.parquet")} == set(TABLES)


def test_official_deadlines_are_versioned_observations():
    """A deadline is stored once per value; a moved deadline is a new version, and an as-of
    reader sees the version known at that time (none before the first observation)."""
    db = FootballDB()
    boot = fixture("bootstrap-static.json")
    t1, t2, t3 = datetime(2026, 8, 1), datetime(2026, 8, 2), datetime(2026, 8, 3)
    ingest_fpl.load_bootstrap(db, boot, 2026, 1, snapshot_at=t1)
    ingest_fpl.load_bootstrap(db, boot, 2026, 1, snapshot_at=t2)
    events = len([e for e in boot["events"] if e.get("deadline_time")])
    assert events and db.con.execute("SELECT count(*) FROM fpl_deadlines").fetchone()[0] == events
    moved = deepcopy(boot)
    moved["events"][0]["deadline_time"] = "2026-08-22T10:00:00Z"
    ingest_fpl.load_bootstrap(db, moved, 2026, 1, snapshot_at=t3)
    assert db.con.execute("SELECT count(*) FROM fpl_deadlines").fetchone()[0] == events + 1
    gw1 = boot["events"][0]["id"]
    first = datetime.fromisoformat(boot["events"][0]["deadline_time"].replace("Z", ""))
    assert official_deadline(db, 2026, gw1, as_of=datetime(2026, 7, 1)) is None
    assert official_deadline(db, 2026, gw1, as_of=t2) == first
    assert official_deadline(db, 2026, gw1, as_of=t3) == datetime(2026, 8, 22, 10)


def test_an_update_records_deadlines_attempts_and_odds_once(alerts):
    db, res = run()
    assert res.exit_code == OK, res.message()
    assert db.counts()["fpl_deadlines"] > 0 and db.counts()["match_odds_log"] > 0
    got = db.query("SELECT * FROM source_attempts ORDER BY seq")
    assert got["source"].tolist() == [r.name for r in res.report.results]
    assert got["attempt_id"].nunique() == 1 and got["ok"].all() and (got.exit_code == OK).all()
    assert got.job.eq("data update").all()
    assert (got["started_at"] <= got["finished_at"]).all() and got["finished_at"].notna().all()
    odds, deadlines = db.counts()["match_odds_log"], db.counts()["fpl_deadlines"]
    run(db=db)  # the same prices and deadlines again add nothing; the attempts are new
    assert db.counts()["match_odds_log"] == odds and db.counts()["fpl_deadlines"] == deadlines
    assert db.query("SELECT count(DISTINCT attempt_id) n FROM source_attempts").n[0] == 2


def test_a_changed_price_is_a_new_odds_observation():
    db = FootballDB()
    csv = (FIX / "fd_E0_2026.csv").read_text()
    ingest_football_data.load_csv(db, csv, "E0", 2026)
    n = db.counts()["match_odds_log"]
    ingest_football_data.load_csv(db, csv, "E0", 2026)
    assert db.counts()["match_odds_log"] == n
    db.con.execute("UPDATE match_odds SET home = home + 0.5")
    moved = db.query("SELECT * FROM match_odds")
    assert db.log_odds(moved) == len(moved) and db.counts()["match_odds_log"] == n + len(moved)


def test_a_critical_failure_is_recorded_with_exit_2(alerts):
    db, res = run({"bootstrap-static": (503, "")})
    assert res.exit_code == CRITICAL_SOURCE_FAILED
    got = db.query("SELECT job, source, ok, critical, exit_code FROM source_attempts ORDER BY seq")
    assert got.to_dict("records") == [
        {"job": "data update", "source": "fpl:bootstrap", "ok": False, "critical": True,
         "exit_code": 2},
    ]  # fmt: skip
    assert db.counts()["fpl_deadlines"] == 0 and alerts


def test_bad_data_stays_exit_3_unexported_alerted_and_is_recorded(alerts, monkeypatch, tmp_path):
    """Exit 3 keeps its behaviour (no export, an alert) and is now visible in source_attempts."""
    monkeypatch.setattr(pipeline, "run_checks", lambda db: [Issue("error", "x", "broken")])
    opts = UpdateOptions(season=2026, football_data=("E0",), export=tmp_path / "out")
    db, res = run(opts=opts)
    assert res.exit_code == BAD_DATA and not (tmp_path / "out").exists()
    assert alerts and "exit 3" in alerts[0]
    codes = db.query("SELECT DISTINCT exit_code FROM source_attempts").exit_code.tolist()
    assert codes == [BAD_DATA]


def test_an_attempt_error_never_keeps_a_query_string():
    db = FootballDB()
    rep = HealthReport([SourceResult("x", False, error="HTTP 403 https://h/p?key=SECRET&a=1")])
    assert pipeline.record_attempts(db, rep, "data update", 0)
    err = db.query("SELECT error FROM source_attempts").error[0]
    assert "SECRET" not in err and err.startswith("HTTP 403 https://h/p")
    assert pipeline.record_attempts(db, HealthReport(), "data update", 0) is None


def _quality_db():
    db = FootballDB()
    boot = fixture("bootstrap-static.json")
    ingest_fpl.load_bootstrap(db, boot, 2026, 7)
    ingest_football_data.load_csv(db, (FIX / "fd_E0_2026.csv").read_text(), "E0", 2026)
    return db


def test_quarantine_is_journalled_and_rolls_back_exactly():
    db = _quality_db()
    mid = db.con.execute("SELECT min(match_id) FROM matches").fetchone()[0]
    db.con.execute("UPDATE matches SET away_team_id = home_team_id WHERE match_id = ?", [mid])
    db.con.execute("UPDATE fpl_players_gw SET now_cost = 5 WHERE fpl_id = 1")
    before = digests(db)
    assert {i.check for i in quarantine(db)} >= {"quarantined_match", "quarantined_prices"}
    assert db.con.execute("SELECT count(*) FROM match_odds_log WHERE match_id = ?",
                          [mid]).fetchone()[0] == 0  # fmt: skip
    rid = db.con.execute("SELECT DISTINCT repair_id FROM repair_log").fetchall()
    assert len(rid) == 1
    restored = rollback_repair(db, rid[0][0])
    assert restored["matches"] == 1 and restored["match_odds_log"] >= 1
    assert digests(db) == before
    assert rollback_repair(db, rid[0][0]) == {}  # a repeat changes nothing


def test_a_merge_repair_is_journalled_and_rolls_back_exactly():
    db = FootballDB()
    mk = db.team_id("fotmob", "1", "Milton Keynes Dons")
    db.con.execute("INSERT INTO teams VALUES (90, 'Peterboro', 'peterboro')")
    db.con.execute("INSERT INTO team_ids VALUES ('football-data', 'Peterboro', 90, 'Peterboro')")
    db.con.execute("INSERT INTO teams VALUES (91, 'Peterborough United', 'peterborough united')")
    db.con.execute("INSERT INTO team_ids VALUES ('fotmob', '8677', 91, 'Peterborough United')")
    fd = db.match_id("football-data", "x", "ENG3", 2026, date(2026, 9, 12), mk, 90,
                     home_goals=1, away_goals=1, finished=True)  # fmt: skip
    db.match_id("fotmob", 5, "ENG3", 2026, date(2026, 9, 12), mk, 91, matchday=6)
    before = digests(db)
    assert any(i.check == "merged_matches" for i in repair(db))
    assert db.query(
        "SELECT match_id FROM matches WHERE competition_id = 'ENG3'"
    ).match_id.tolist() == [fd]
    for (rid,) in db.con.execute(
        "SELECT DISTINCT repair_id FROM repair_log ORDER BY 1 DESC"
    ).fetchall():
        rollback_repair(db, rid)
    assert digests(db) == before
