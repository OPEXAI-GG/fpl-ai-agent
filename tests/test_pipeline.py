"""Failure handling: a source that is down or has changed shape is skipped and reported; the
agent never crashes and never writes or exports bad data."""

import httpx
import pytest

from fpl_agent import notify
from fpl_agent.db import ingest_football_data
from fpl_agent.db.database import FootballDB
from fpl_agent.pipeline import BAD_DATA, CRITICAL_SOURCE_FAILED, OK, UpdateOptions, run_update
from tests.helpers import clients, fixture

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


def status(result):
    return {r.name: r.ok for r in result.report.results}


def test_happy_path(alerts, tmp_path):
    opts = UpdateOptions(season=2026, football_data=("E0", "E1"), points=True, players=True,
                         export=tmp_path / "out")  # fmt: skip
    db, res = run(opts=opts)
    assert res.exit_code == OK, res.message()
    assert all(status(res).values()), res.message()
    assert db.counts()["matches"] > 0 and db.counts()["fpl_points_gw"] > 0
    assert (tmp_path / "out" / "matches.parquet").exists()
    assert alerts == []  # a clean run sends no alert


def test_rerun_is_idempotent(alerts):
    db, _ = run()
    # snapshots accumulate by design (point-in-time); logs record every run
    skip = {"ingest_log", "fpl_players_gw", "source_attempts"}
    first = {k: v for k, v in db.counts().items() if k not in skip}
    snaps = db.counts()["fpl_players_gw"]
    run(db=db)
    assert {k: v for k, v in db.counts().items() if k not in skip} == first
    assert db.counts()["fpl_players_gw"] == 2 * snaps


@pytest.mark.parametrize(
    "response",
    [
        (503, "Service Unavailable"),
        (200, "<html>The game is being updated.</html>"),
        (200, {"events": []}),  # format changed: required keys gone
        (200, []),
        (404, ""),
    ],
)
def test_fpl_down_or_changed_aborts_safely(alerts, response):
    db, res = run({"bootstrap-static": response})
    assert res.exit_code == CRITICAL_SOURCE_FAILED
    assert res.report.critical_failed
    assert db.counts()["players"] == 0  # nothing written
    assert alerts and "needs attention" in alerts[0]


def test_fpl_bootstrap_missing_field_is_caught(alerts):
    boot = fixture("bootstrap-static.json")
    for e in boot["elements"]:
        del e["now_cost"]  # FPL renames a field
    db, res = run({"bootstrap-static": (200, boot)})
    assert res.exit_code == CRITICAL_SOURCE_FAILED and db.counts()["players"] == 0


@pytest.mark.parametrize(
    "response", [(500, "boom"), (200, "<html>blocked</html>"), (200, {"x": 1})]
)
def test_understat_failure_is_skipped_not_fatal(alerts, response):
    db, res = run({"getLeagueData": response})
    assert res.exit_code == OK  # the rest of the update still happened
    assert status(res)["understat:league"] is False
    assert status(res)["fpl:players"] and status(res)["football-data:E1"]
    assert db.counts()["players"] > 0 and db.counts()["matches"] > 0
    assert db.query("SELECT count(*) n FROM team_match WHERE source = 'understat'").n[0] == 0
    assert alerts  # reported


@pytest.mark.parametrize(
    "response",
    [
        (200, "<html>Cloudflare</html>"),
        (404, ""),
        (200, "Date,Home,Away\n01/01/2026,A,B\n"),  # column names changed
        (200, ""),
    ],
)
def test_football_data_failure_is_skipped_not_fatal(alerts, response):
    db, res = run({"E1.csv": response})
    assert res.exit_code == OK
    assert status(res)["football-data:E1"] is False
    assert status(res)["football-data:E0"] is True
    assert db.query("SELECT count(*) n FROM matches WHERE competition_id = 'ENG2'").n[0] == 0
    assert alerts


def test_bad_values_block_export_and_alert(alerts, tmp_path):
    csv = tmp_path / "bad.csv"
    lines = fixture_text("fd_E1_2026.csv").splitlines()
    header = lines[0].split(",")
    row = lines[1].split(",")
    row[header.index("AvgCH")] = "0.4"  # decimal odds below 1.0 are impossible
    csv.write_text("\n".join([lines[0], ",".join(row)]))
    opts = UpdateOptions(season=2026, football_data=("E1",), export=tmp_path / "out")
    db, res = run({"E1.csv": (200, csv.read_text())}, opts=opts)
    assert res.exit_code == BAD_DATA
    assert any(i.check == "odds_below_one" for i in res.issues)
    assert not (tmp_path / "out").exists()  # bad data is never exported
    assert alerts


def fixture_text(name):
    from tests.helpers import FIX

    return (FIX / name).read_text()


def test_unexpected_exception_in_one_source_does_not_crash(alerts, monkeypatch):
    def boom(*a, **k):
        raise ZeroDivisionError("bug in parser")

    monkeypatch.setattr(ingest_football_data, "load_csv", boom)
    db, res = run()
    assert res.exit_code == OK
    failed = [r for r in res.report.results if not r.ok]
    assert {r.name for r in failed} == {"football-data:E0", "football-data:E1"}
    assert "unexpected ZeroDivisionError" in failed[0].error


def test_partial_source_failure_rolls_back(alerts, monkeypatch):
    real = ingest_football_data.load_csv

    def half_then_die(db, text, div, season):
        real(db, text, div, season)
        raise RuntimeError("died after writing")

    monkeypatch.setattr(ingest_football_data, "load_csv", half_then_die)
    db, res = run()
    assert (
        db.counts()["matches"]
        == db.query("SELECT count(*) n FROM matches WHERE source IN ('fpl','understat')").n[0]
    )  # nothing from football-data survived the failure


def test_points_mismatch_is_flagged(alerts):
    boot = fixture("bootstrap-static.json")
    from tests.helpers import live_payload

    live = live_payload(boot)
    live["elements"][0]["stats"]["total_points"] += 3  # FPL changes a scoring rule
    opts = UpdateOptions(season=2026, football_data=(), points=True)
    db, res = run({"/live/": (200, live)}, opts=opts)
    assert res.exit_code == BAD_DATA
    assert any(i.check == "points_vs_rules" for i in res.issues)


def test_live_feed_unavailable_skips_points(alerts):
    opts = UpdateOptions(season=2026, football_data=(), points=True)
    db, res = run({"/live/": (503, "")}, opts=opts)
    assert res.exit_code == OK
    assert db.counts()["fpl_points_gw"] == 0
    assert any(not r.ok and r.name.startswith("fpl:points") for r in res.report.results)


def test_notify_never_raises(monkeypatch):
    def fail(request):
        raise httpx.ConnectError("down")

    client = httpx.Client(transport=httpx.MockTransport(fail))
    assert notify.send("x", client=client, webhook="https://example.invalid/hook") is False
    assert notify.send("x", webhook="") is False
