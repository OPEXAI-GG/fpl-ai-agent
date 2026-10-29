"""Source health keeps failed, stale, actual-zero and never-run sources apart; the eligibility
manifest only calls a gameweek genuine evidence when its inputs were observed before the
deadline; both reports are read-only."""

import json
from datetime import datetime, timedelta

import pandas as pd
import pytest

from fpl_agent.cli import main
from fpl_agent.db import eligibility, source_health
from fpl_agent.db.database import FootballDB
from fpl_agent.db.quality import run_checks, source_conflicts
from tests.test_execution import base  # noqa: F401  (fixture)

AT = datetime(2026, 10, 20, 12)


def attempt(db, source, ok, rows, hours_ago, critical=False, seq=[0]):  # noqa: B006
    seq[0] += 1
    t = AT - timedelta(hours=hours_ago)
    db.con.execute("INSERT INTO source_attempts VALUES (?, ?, 'data update', ?, ?, ?, ?, ?, ?, "
                   "?, 0)", [f"a{seq[0]}", 1, source, ok, critical, rows,
                             None if ok else "HTTP 503", t, t])  # fmt: skip


def snapshot(db, gw, at, fpl_id=1, observed=True, season=2026):
    raw = {"element_type": 3, "id": fpl_id} if observed else {"was_home": True}
    db.upsert("fpl_players_gw", pd.DataFrame([dict(
        season=season, gw=gw, snapshot_at=at, fpl_id=fpl_id, player_id=fpl_id, position="MID",
        now_cost=50, status="a", team_id=1, raw=json.dumps(raw))]))  # fmt: skip


def test_each_source_state_is_distinct():
    db = FootballDB()
    for src in source_health.CRITICAL:
        attempt(db, src, True, 700, 1, critical=True)
    attempt(db, "understat:league", True, 20, 30)  # good once, then failed twice
    attempt(db, "understat:league", False, None, 5)
    attempt(db, "understat:league", False, None, 2)
    attempt(db, "football-data:E0", True, 0, 3)  # succeeded and found nothing new
    attempt(db, "fotmob:ENG1", True, 15, 40)  # last success too long ago
    attempt(db, "fpl:points gw7", True, 600, 2)
    snapshot(db, 8, AT - timedelta(hours=1))
    r = source_health.report(db, 2026, AT)
    state = {s["source"]: s for s in r["sources"]}
    assert state["understat:league"]["state"] == "failed"
    assert state["understat:league"]["failures_since_success"] == 2
    assert state["understat:league"]["last_error"] == "HTTP 503"
    assert state["understat:league"]["last_success_at"] is not None
    assert state["football-data:E0"]["state"] == "ok_zero"
    assert state["fotmob:ENG1"]["state"] == "stale"
    assert state["fpl:points gw7"]["state"] == "ok"
    assert r["overall"] == "degraded" and not r["blocked"]
    assert "understat:league failed" in r["degraded"] and "fotmob:ENG1 stale" in r["degraded"]


def test_a_failed_critical_source_blocks_and_a_missing_one_is_unknown():
    db = FootballDB()
    snapshot(db, 8, AT - timedelta(hours=1))
    attempt(db, "fpl:bootstrap", True, 700, 3, critical=True)
    attempt(db, "fpl:bootstrap", False, None, 1, critical=True)
    r = source_health.report(db, 2026, AT)
    assert r["overall"] == "blocked"
    assert r["blocked"] == ["fpl:bootstrap failed"]
    # no record is unknown, not a failure: said, but not a block
    assert {"fpl:fixtures never (no recorded attempt)",
            "fpl:players never (no recorded attempt)"} <= set(r["degraded"])  # fmt: skip


def test_data_checks_see_stale_snapshots_and_missing_points():
    db = FootballDB()
    for src in source_health.CRITICAL:
        attempt(db, src, True, 700, 1, critical=True)
    snapshot(db, 8, AT - timedelta(hours=30))
    snapshot(db, 8, AT + timedelta(hours=5), fpl_id=2)  # not yet taken at AT: ignored
    db.con.execute("INSERT INTO matches (match_id, competition_id, season, matchday, finished, "
                   "home_team_id, away_team_id) VALUES (1, 'ENG1', 2026, 7, true, 1, 2)")  # fmt: skip
    r = source_health.report(db, 2026, AT)
    assert r["data"]["fpl_snapshot"]["state"] == "stale"
    assert r["data"]["fpl_snapshot"]["age_hours"] == 30
    assert r["data"]["fpl_points"]["finished_gws_without_points"] == [7]
    assert r["data"]["fotmob_detail"]["state"] == "partial"
    assert r["overall"] == "blocked" and "FPL snapshot stale" in r["blocked"]


def test_reports_are_read_only_and_say_what_an_older_file_cannot_show(tmp_path, capsys):
    path = tmp_path / "old.duckdb"
    db = FootballDB(path)
    snapshot(db, 8, datetime(2026, 10, 1))
    db.con.execute("DROP TABLE source_attempts")
    db.con.execute("DROP TABLE fpl_deadlines")
    db.close()
    before = path.read_bytes()
    ro = FootballDB(path, schema="read")
    assert ro.missing_tables == ["source_attempts", "fpl_deadlines"] and ro.created_tables == []
    with pytest.raises(Exception, match="read-only"):
        ro.con.execute("CREATE TABLE x (a INTEGER)")
    r = source_health.report(ro, 2026, AT)
    assert r["attempt_record"] is False and r["sources"] == []
    assert any("no source attempt record" in d for d in r["degraded"])
    assert eligibility.manifest(ro, 2026)["missing_tables"] == ro.missing_tables
    ro.close()
    assert main(["source-health", "--db", str(path)]) == 2  # stale snapshot: blocked
    assert json.loads(capsys.readouterr().out)["overall"] == "blocked"
    assert main(["db-eligibility", "--db", str(path)]) == 0
    assert json.loads(capsys.readouterr().out)["season"] == 2026
    assert path.read_bytes() == before  # neither report wrote anything


def points(db, gw, kickoff, fpl_ids=(1, 2), fixture=None):
    db.upsert("fpl_points_gw", pd.DataFrame([dict(
        season=2026, gw=gw, fpl_id=f, player_id=f, fixture_id=fixture or gw, minutes=90,
        total_points=2, kickoff=kickoff, stats='{"goals_scored": 0}') for f in fpl_ids]))  # fmt: skip


def test_only_inputs_observed_before_an_officially_recorded_deadline_are_eligible():
    db = FootballDB()
    ko = {g: datetime(2026, 8, 7 * g, 15) for g in (1, 2, 3, 4)}
    dl = {g: ko[g] - timedelta(minutes=90) for g in ko}
    for g in ko:
        points(db, g, ko[g])
    # gw1: deadline recorded beforehand, both players observed before it -> eligible
    db.con.execute(
        "INSERT INTO fpl_deadlines VALUES (2026, 1, ?, ?)", [dl[1], dl[1] - timedelta(days=3)]
    )
    for f in (1, 2):
        snapshot(db, 1, dl[1] - timedelta(hours=2), fpl_id=f)
    # gw2: snapshots rebuilt from the archive (kickoff-stamped) -> reconstructed
    db.con.execute(
        "INSERT INTO fpl_deadlines VALUES (2026, 2, ?, ?)", [dl[2], dl[2] - timedelta(days=3)]
    )
    for f in (1, 2):
        snapshot(db, 2, ko[2], fpl_id=f, observed=False)
    # gw3: observed snapshot, but the deadline was only recorded after it passed -> reconstructed
    db.con.execute(
        "INSERT INTO fpl_deadlines VALUES (2026, 3, ?, ?)", [dl[3], dl[3] + timedelta(days=1)]
    )
    for f in (1, 2):
        snapshot(db, 3, dl[3] - timedelta(hours=2), fpl_id=f)
    # gw4: nothing observed -> unavailable; a snapshot taken after the deadline does not count
    snapshot(db, 4, dl[4] + timedelta(hours=1), fpl_id=1)
    m = eligibility.manifest(db, 2026)
    assert m["eligible_gws"] == [1]
    assert m["reconstructed_gws"] == [2, 3]
    assert m["unavailable_gws"] == [4]
    g = {r["gw"]: r for r in m["gameweeks"]}
    assert g[3]["cutoff_kind"] == "approximate" and g[1]["cutoff_kind"] == "official"
    assert any("rebuilt from the archive" in x for x in g[2]["reasons"])
    assert g[4]["covered_players"] == 0 and g[4]["required_players"] == 2
    # one player missing from the observed snapshot: below coverage, no longer eligible
    db.con.execute("DELETE FROM fpl_players_gw WHERE gw = 1 AND fpl_id = 2")
    assert eligibility.gameweek(db, 2026, 1)["class"] == "reconstructed"


def test_unresolved_fpl_fotmob_conflicts_are_retained_and_excluded():
    db = FootballDB()
    points(db, 1, datetime(2026, 8, 7, 15), fpl_ids=(1,), fixture=11)
    db.con.execute("UPDATE fpl_points_gw SET stats = '{\"goals_scored\": 1}', minutes = 77")
    db.con.execute("INSERT INTO matches (match_id, season) VALUES (40, 2026)")
    db.con.execute("INSERT INTO match_ids VALUES ('fpl-2026', '11', 40)")
    db.con.execute("INSERT INTO player_match_stats VALUES (40, 1, 'fotmob', 'goals', 2, NULL)")
    db.con.execute("INSERT INTO appearances (match_id, player_id, source, minutes) "
                   "VALUES (40, 1, 'fotmob', 78)")  # fmt: skip
    found = source_conflicts(db, 2026)
    assert [(c["match_id"], c["kinds"]) for c in found] == [(40, ["goals"])]
    assert eligibility.manifest(db, 2026)["excluded_matches"] == [40]
    issues = {i.check: i for i in run_checks(db)}
    assert issues["fpl_fotmob_conflict"].severity == "warn"  # retained, not corrected
    db.con.execute("UPDATE appearances SET minutes = 70")  # 7 minutes apart as well
    assert source_conflicts(db, 2026)[0]["kinds"] == ["goals", "minutes"]
    db.con.execute("UPDATE player_match_stats SET value = 1")
    db.con.execute("UPDATE appearances SET minutes = 80")  # within the stoppage tolerance
    assert source_conflicts(db, 2026) == []
    assert "fpl_fotmob_conflict" not in {i.check for i in run_checks(db)}


def test_history_rows_say_whether_their_snapshot_was_observed_or_reconstructed():
    from fpl_agent.model import features, project

    db = FootballDB()
    points(db, 1, datetime(2026, 8, 7, 15))
    snapshot(db, 1, datetime(2026, 8, 7, 15), fpl_id=1, observed=False)
    snapshot(db, 1, datetime(2026, 8, 6, 10), fpl_id=2)
    f = features.player_gw_frame(db).set_index("player_id")
    assert f.loc[1, "snapshot_kind"] == "reconstructed" and f.loc[2, "snapshot_kind"] == "observed"
    early = features.snapshot_as_of(f.reset_index(), db, datetime(2026, 8, 7, 12))
    assert early.set_index("player_id").loc[1, "snapshot_kind"] is None or pd.isna(
        early.set_index("player_id").loc[1, "snapshot_kind"])  # fmt: skip
    assert project.history_inputs(f.reset_index())["reconstructed_snapshot"] == 1


def test_health_is_evaluated_as_of_its_time():
    """Codex repro: failure 11:00, recovery 13:00, report at 12:00 says failed, with nothing
    from after 12:00; unfinished attempts are not outcomes; ties and the reported rows/error
    come from one row."""
    db = FootballDB()
    for src in source_health.CRITICAL:
        attempt(db, src, True, 700, 30, critical=True)  # 06:00 the day before
    day = AT.replace(hour=0)

    def row(aid, seq, ok, rows, at, err=None, finished=True):
        db.con.execute("INSERT INTO source_attempts VALUES (?, ?, 'data update', 'fpl:players', "
                       "?, true, ?, ?, ?, ?, 0)", [aid, seq, ok, rows, err, at,
                                                    at if finished else None])  # fmt: skip

    row("b1", 1, False, None, day.replace(hour=11), "HTTP 503")
    row("b2", 1, True, 668, day.replace(hour=13))
    at12 = day.replace(hour=12)
    s12 = {x["source"]: x for x in source_health.report(db, 2026, at12)["sources"]}["fpl:players"]
    assert s12["state"] == "failed" and s12["last_error"] == "HTTP 503"
    assert s12["last_attempt_at"] == day.replace(hour=11).isoformat()
    assert s12["last_success_at"] < at12.isoformat()  # the 13:00 recovery is invisible
    assert all(v is None or not isinstance(v, str) or v <= at12.isoformat()
               for v in s12.values() if isinstance(v, str) and v[:2] == "20")  # fmt: skip
    s14 = {x["source"]: x for x in source_health.report(db, 2026, day.replace(hour=14))
           ["sources"]}["fpl:players"]  # fmt: skip
    assert s14["state"] == "ok" and s14["last_rows"] == 668 and s14["last_error"] is None
    # a running attempt (no finish) is counted as unfinished, never as the outcome
    row("b3", 1, False, None, day.replace(hour=14), finished=False)
    s15 = {x["source"]: x for x in source_health.report(db, 2026, day.replace(hour=15))
           ["sources"]}["fpl:players"]  # fmt: skip
    assert s15["state"] == "ok" and s15["unfinished"] == 1
    # same finish time: the later attempt id wins, and its own rows/error are reported
    row("b4", 1, True, 0, day.replace(hour=16))
    row("b5", 1, False, None, day.replace(hour=16), "timeout")
    s17 = {x["source"]: x for x in source_health.report(db, 2026, day.replace(hour=17))
           ["sources"]}["fpl:players"]  # fmt: skip
    assert s17["state"] == "failed" and s17["last_error"] == "timeout" and s17["last_rows"] is None


def _gw_with(db, scorers, snapshot_ids, pulls=None):
    """gw 1 with official deadline recorded in time; `scorers` score; `snapshot_ids` are in one
    observed pre-deadline pull (or `pulls`: [(hours before deadline, ids)])."""
    ko = datetime(2026, 8, 15, 15)
    dl = ko - timedelta(minutes=90)
    db.upsert("fpl_points_gw", pd.DataFrame([dict(
        season=2026, gw=1, fpl_id=f, player_id=f, fixture_id=1, minutes=90, total_points=2,
        kickoff=ko) for f in scorers]))  # fmt: skip
    db.con.execute("INSERT INTO fpl_deadlines VALUES (2026, 1, ?, ?)", [dl, dl - timedelta(days=2)])
    for hours, ids in pulls or [(2, snapshot_ids)]:
        for f in ids:
            snapshot(db, 1, dl - timedelta(hours=hours), fpl_id=f)
    return eligibility.gameweek(db, 2026, 1)


def test_coverage_counts_the_scoring_players_themselves():
    """Codex repro: scorers 1/2, snapshot only 3/4 -> zero coverage, not eligible."""
    g = _gw_with(FootballDB(), [1, 2], [3, 4])
    assert (g["required_players"], g["covered_players"], g["missing_players"]) == (2, 0, 2)
    assert g["class"] == "unavailable"
    g = _gw_with(FootballDB(), [1, 2], [1, 2, 3, 4])  # extra players do not matter
    assert (g["covered_players"], g["missing_players"], g["class"]) == (2, 0, "eligible")
    g = _gw_with(FootballDB(), [1, 2], [1, 3])  # partial overlap
    assert (g["covered_players"], g["class"]) == (1, "reconstructed")


@pytest.mark.parametrize("covered,cls", [(19, "eligible"), (18, "reconstructed")])
def test_the_coverage_threshold_boundary(covered, cls):
    g = _gw_with(FootballDB(), list(range(1, 21)), list(range(1, covered + 1)))
    assert g["coverage"] == covered / 20 and g["class"] == cls


def test_partial_pulls_are_not_pooled():
    """Two partial fetches (player 1 in the earlier, player 2 in the later) are not one
    complete snapshot: only the latest pull by the deadline counts."""
    g = _gw_with(FootballDB(), [1, 2], None, pulls=[(5, [1]), (2, [2])])
    assert g["covered_players"] == 1 and g["class"] == "reconstructed"
    assert g["snapshot_used"] == (datetime(2026, 8, 15, 13, 30) - timedelta(hours=2)).isoformat()


def test_a_validated_backtest_uses_only_eligible_gameweeks_and_drops_excluded_matches(
    monkeypatch,
):
    """The filters are enforced, not printed: a non-eligible gameweek is refused; in an eligible
    one, every row of a conflicting match (history and the scored week) is absent from what
    the projection receives and from scoring."""
    from fpl_agent.db.eligibility import NotEligible
    from fpl_agent.model import backtest, project
    from fpl_agent.model.simulate import SimConfig
    from tests.synth import build_world

    db = build_world(n_teams=6, seasons=(2023, 2024), seed=7).db
    gw = 10
    with pytest.raises(NotEligible, match="not eligible"):
        backtest.projection_backtest(db, 2024, [gw], SimConfig(n=40), validated=True)
    ko = db.con.execute("SELECT min(kickoff) FROM fpl_points_gw WHERE season = 2024 AND gw = ?",
                        [gw]).fetchone()[0]  # fmt: skip
    dl = ko - timedelta(minutes=90)
    db.con.execute(
        "INSERT INTO fpl_deadlines VALUES (2024, ?, ?, ?)", [gw, dl, dl - timedelta(days=2)]
    )
    db.con.execute("UPDATE fpl_players_gw SET raw = ?, snapshot_at = ? WHERE season = 2024 "
                   "AND gw = ?", [json.dumps({"element_type": 2}), dl - timedelta(hours=3), gw])  # fmt: skip
    # one conflicting match in the history (gw 4) and one in the scored week
    bad = []
    for g in (4, gw):
        mid, pid = db.con.execute(
            "SELECT fixture_id, player_id FROM fpl_points_gw WHERE season = 2024 AND gw = ? AND "
            "minutes > 0 ORDER BY 1, 2 LIMIT 1", [g]).fetchone()  # fmt: skip
        db.con.execute("INSERT INTO match_ids VALUES ('fpl-2024', ?, ?)", [str(mid), mid])
        db.con.execute("INSERT OR REPLACE INTO player_match_stats VALUES (?, ?, 'fotmob', 'goals', "
                       "7, NULL)", [mid, pid])  # fmt: skip
        bad.append(mid)
    from fpl_agent.db.eligibility import manifest

    m = manifest(db, 2024)
    assert gw in m["eligible_gws"] and sorted(m["excluded_matches"]) == sorted(bad)
    seen = {}
    real = project.build_projection

    def capture(*a, **k):
        seen["frame"], seen["pm"] = k["frame"], k["pm"]
        return real(*a, **k)

    monkeypatch.setattr(project, "build_projection", capture)
    res = backtest.projection_backtest(db, 2024, [gw], SimConfig(n=40), validated=True)
    assert not seen["frame"]["fixture_id"].isin(bad).any()
    assert not seen["pm"]["match_id"].isin(bad).any()
    played = set(db.con.execute(
        "SELECT player_id FROM fpl_points_gw WHERE season = 2024 AND gw = ? AND fixture_id = ?",
        [gw, bad[1]]).fetchall())  # fmt: skip
    assert len(res) and not set(res["player_id"]) & {p for (p,) in played}
    explore = backtest.projection_backtest(db, 2024, [gw], SimConfig(n=40))
    assert set(explore["player_id"]) & {p for (p,) in played}  # without validation: scored


def test_a_failed_critical_source_is_a_rail_in_every_mode(base):  # noqa: F811
    from fpl_agent.execution import safety
    from tests.test_execution import RULES, ctx_for, rules_hit

    d = base[2]
    for mode in ("dry", "live"):
        ctx = ctx_for(base, mode=mode, critical_source_failures=("fpl:players failed at x",))
        v = safety.check(d, ctx, RULES)
        assert rules_hit(v, "critical_source_failed") and v.blocked("lineup")
        assert not rules_hit(safety.check(d, ctx_for(base, mode=mode), RULES),
                             "critical_source_failed")  # fmt: skip


@pytest.mark.skipif(
    __import__("platform").system() == "Linux", reason="the guard targets non-Linux"
)
def test_archive_scripts_refuse_where_they_are_unverified(tmp_path):
    import shutil
    import subprocess
    from pathlib import Path

    bash = shutil.which("bash")
    if not bash:
        pytest.skip("no bash to run the scripts with")
    root = Path(__file__).parent.parent
    calls = tmp_path / "calls"
    gh = tmp_path / "gh.sh"
    gh.write_text(f'#!/usr/bin/env bash\necho "$*" >> "{calls.as_posix()}"\n', encoding="utf-8")
    for script in ("data_restore.sh", "data_publish.sh"):
        r = subprocess.run([bash, (root / "scripts" / script).as_posix(), (tmp_path / "x").as_posix()],
                           env={**__import__("os").environ, "GH": f"bash {gh.as_posix()}",
                                "VERIFY": "true"}, capture_output=True, text=True)  # fmt: skip
        assert r.returncode == 2 and "unsupported environment" in r.stderr, (script, r.stderr)
    assert not calls.exists()  # the release was never touched
