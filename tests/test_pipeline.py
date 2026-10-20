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
    skip = {"ingest_log", "fpl_players_gw"}  # snapshots accumulate by design (point-in-time)
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
    csv.write_text("\n".join([lines[0], ",".join(row)]), encoding="utf-8")
    opts = UpdateOptions(season=2026, football_data=("E1",), export=tmp_path / "out")
    db, res = run({"E1.csv": (200, csv.read_text(encoding="utf-8"))}, opts=opts)
    assert res.exit_code == BAD_DATA
    assert any(i.check == "odds_below_one" for i in res.issues)
    assert not (tmp_path / "out").exists()  # bad data is never exported
    assert alerts


def fixture_text(name):
    from tests.helpers import FIX

    return (FIX / name).read_text(encoding="utf-8")


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


@pytest.mark.parametrize("response", [(503, ""), (200, []), (200, [{"id": 1}])])
def test_fixtures_failure_is_critical_and_writes_nothing(alerts, tmp_path, response):
    opts = UpdateOptions(season=2026, football_data=("E0",), export=tmp_path / "out")
    db, res = run({"fixtures": response}, opts=opts)
    assert res.exit_code == CRITICAL_SOURCE_FAILED and res.report.critical_failed
    assert db.counts()["players"] == 0 and db.counts()["matches"] == 0
    assert not (tmp_path / "out").exists()


def test_player_load_failure_fails_the_run(alerts, monkeypatch, tmp_path):
    def boom(*a, **k):
        raise RuntimeError("team id lookup broke")

    monkeypatch.setattr(FootballDB, "team_id", boom)
    opts = UpdateOptions(season=2026, football_data=(), export=tmp_path / "out")
    db, res = run(opts=opts)
    assert res.exit_code == CRITICAL_SOURCE_FAILED and res.report.critical_failed
    assert db.counts()["fpl_players_gw"] == 0  # the transaction rolled back
    assert not (tmp_path / "out").exists()
    assert [r.name for r in res.report.results][-1] == "fpl:players"  # nothing ran after it


def test_malformed_later_row_is_caught(alerts):
    boot = fixture("bootstrap-static.json")
    del boot["elements"][-1]["now_cost"]
    db, res = run({"bootstrap-static": (200, boot)})
    assert res.exit_code == CRITICAL_SOURCE_FAILED and db.counts()["players"] == 0


def test_failed_bootstrap_leaves_database_untouched(alerts):
    db = FootballDB()
    before = db.counts()
    db, res = run({"bootstrap-static": (503, "")}, db=db)
    assert res.exit_code == CRITICAL_SOURCE_FAILED
    assert db.counts() == before  # not even the one-off repairs ran
    assert db.query("SELECT count(*) n FROM ingest_state WHERE source = 'repair'").n[0] == 0


def test_empty_feed_for_finished_gameweek_is_rejected(alerts):
    opts = UpdateOptions(season=2026, football_data=(), points=True)
    db, res = run({"/live/": (200, {"elements": []})}, opts=opts)
    assert db.counts()["fpl_points_gw"] == 0
    assert any(not r.ok and r.name.startswith("fpl:points") for r in res.report.results)


# ---- fixture-level official history (double gameweeks, blanks, reloads) -------------------
def _dgw_live():
    from tests.helpers import live_payload

    live = live_payload(fixture("bootstrap-static.json"))
    el = live["elements"][0]
    el["stats"].update(minutes=180, total_points=9, bps=40, bonus=2, assists=1,
                       expected_goals="0.80")  # fmt: skip
    el["explain"] = [
        {"fixture": 1, "stats": [{"identifier": "minutes", "points": 2, "value": 90},
                                 {"identifier": "bonus", "points": 2, "value": 2},
                                 {"identifier": "bps", "points": 0, "value": 25}]},
        {"fixture": 4, "stats": [{"identifier": "minutes", "points": 2, "value": 90},
                                 {"identifier": "assists", "points": 3, "value": 1},
                                 {"identifier": "bps", "points": 0, "value": 15}]},
    ]  # fmt: skip
    return live, el["id"]


def test_double_gameweek_totals_reconcile_exactly(alerts):
    live, fpl_id = _dgw_live()
    opts = UpdateOptions(season=2026, football_data=(), points=True)
    db, res = run({"/live/": (200, live)}, opts=opts)
    rows = db.query("SELECT fixture_id, minutes, total_points, bps, bonus, stats "
                    "FROM fpl_points_gw WHERE gw = 1 AND fpl_id = ? ORDER BY fixture_id",
                    [fpl_id])  # fmt: skip
    assert rows.fixture_id.tolist() == [1, 4]
    assert rows.minutes.tolist() == [90, 90]  # not 180 each
    assert (rows.minutes.sum(), rows.total_points.sum(), rows.bps.sum(), rows.bonus.sum()) == (
        180, 9, 40, 2)  # fmt: skip
    import json

    stats = [json.loads(s) for s in rows.stats]
    assert [s["assists"] for s in stats] == [0, 1]
    assert [s["expected_goals"] for s in stats] == [None, None]  # unknown, not copied or halved


def test_split_stats_never_invents_values():
    from fpl_agent.db.ingest_fpl import split_stats

    per, unknown = split_stats({"minutes": 120, "starts": 2, "goals_scored": 0},
                               {1: [{"identifier": "minutes", "value": 90, "points": 2}]},
                               [1, 2])  # fmt: skip
    # explain accounts for 90 of 120 minutes: the rest can't be placed, so both stay unknown
    assert per[1]["minutes"] is None and per[2]["minutes"] is None
    assert per[1]["starts"] is None and per[1]["goals_scored"] == 0 == per[2]["goals_scored"]
    assert unknown == 2


def test_blank_and_zero_minute_rows(alerts):
    from fpl_agent.db import ingest_fpl
    from fpl_agent.model import features

    db, _ = run(opts=UpdateOptions(season=2026, football_data=()))
    boot = fixture("bootstrap-static.json")
    team1 = [e["id"] for e in boot["elements"] if e["team"] == 1][:1]
    zero = {"minutes": 0, "total_points": 0, "bps": 0, "bonus": 0}
    live = {"elements": [{"id": team1[0], "stats": dict(zero), "explain": []}]}
    with db.transaction():
        ingest_fpl.load_live(db, live, 2026, 1)  # team 1 played fixture 1 in GW1
    before = features.player_gw_frame(db)
    assert db.query(
        "SELECT fixture_id, minutes FROM fpl_points_gw WHERE gw = 1"
    ).values.tolist() == [[1, 0]]  # a real zero-minute appearance, kept for training
    assert len(before) == 1
    with db.transaction():
        ingest_fpl.load_live(db, live, 2026, 37)  # no fixtures stored for GW37: a blank
    assert db.query("SELECT fixture_id FROM fpl_points_gw WHERE gw = 37").fixture_id.tolist() == [0]
    after = features.player_gw_frame(db)
    assert after.equals(before)  # the blank placeholder never reaches training


def test_truncated_feed_cannot_erase_history(alerts):
    from tests.helpers import live_payload

    opts = UpdateOptions(season=2026, football_data=(), points=True)
    db, _ = run(opts=opts)
    before = db.counts()["fpl_points_gw"]
    live = live_payload(fixture("bootstrap-static.json"))
    live["elements"] = live["elements"][:3]
    reload = UpdateOptions(season=2026, football_data=(), points=True, reload_gws=(1,))
    _, res = run({"/live/": (200, live)}, opts=reload, db=db)
    assert db.counts()["fpl_points_gw"] == before
    assert any(not r.ok and r.name == "fpl:points gw1" for r in res.report.results)


def test_points_refresh_is_idempotent_and_loads_new_gameweeks(alerts):
    opts = UpdateOptions(season=2026, football_data=(), points=True)
    db, _ = run(opts=opts)
    first = db.query("SELECT gw, count(*) n FROM fpl_points_gw GROUP BY gw ORDER BY gw")
    run(opts=opts, db=db)
    assert db.query("SELECT gw, count(*) n FROM fpl_points_gw GROUP BY gw ORDER BY gw").equals(
        first)  # fmt: skip
    boot = fixture("bootstrap-static.json")
    new = max(first.gw) + 1
    for ev in boot["events"]:
        if ev["id"] == new:
            ev["finished"] = True  # a midweek gameweek has just finished
    run({"bootstrap-static": (200, boot)}, opts=opts, db=db)
    assert new in db.query("SELECT DISTINCT gw FROM fpl_points_gw").gw.tolist()


def test_finalised_gameweeks_are_not_refetched(alerts):
    boot = fixture("bootstrap-static.json")
    for ev in boot["events"]:
        ev["data_checked"] = ev["finished"]
    opts = UpdateOptions(season=2026, football_data=(), points=True)
    db, _ = run({"bootstrap-static": (200, boot)}, opts=opts)
    _, res = run({"bootstrap-static": (200, boot)}, opts=opts, db=db)
    assert not any(r.name.startswith("fpl:points") for r in res.report.results)
