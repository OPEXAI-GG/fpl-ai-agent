"""Trustworthy evaluation: reviews score one eligible pre-deadline run, double gameweeks keep
both fixtures' components, provisional results never teach anything, missing results stay
missing, and every projection input honours the run's cutoff."""

import json
from datetime import date, datetime, timedelta

import pandas as pd
import pytest

from fpl_agent import pit, review
from fpl_agent.db.database import FootballDB
from fpl_agent.model import calibration, project

DEADLINE = datetime(2026, 10, 23, 17, 30)


def _db(final: bool = True, checked: bool = True) -> FootballDB:
    db = FootballDB()
    db.append("players", pd.DataFrame({"player_id": [1, 2, 3], "name": ["A", "B", "C"],
                                       "position": ["MID", "MID", "FWD"]}))  # fmt: skip
    db.upsert("fpl_events", pd.DataFrame([{"season": 2026, "gw": 8, "deadline_time": DEADLINE,
                                           "finished": final, "data_checked": checked,
                                           "observed_at": DEADLINE}]))  # fmt: skip
    pts = []
    for pid, total in ((1, 6), (2, 2)):
        pts.append({"season": 2026, "gw": 8, "fpl_id": pid, "player_id": pid, "fixture_id": 80,
                    "minutes": 90, "total_points": total,
                    "explain": json.dumps([{"identifier": "minutes", "points": 2, "value": 90}])})  # fmt: skip
    db.append("fpl_points_gw", pd.DataFrame(pts))  # player 3 has no result row at all
    return db


def _run(db, run_id, at, xpts: dict[int, float], register=True, cutoff=None):
    db.append("projections", pd.DataFrame([
        {"run_id": run_id, "model": "sim-v1", "created_at": at, "season": 2026, "gw": 8,
         "player_id": p, "xpts": x, "detail": json.dumps({"components": {"minutes": 2.0}})}
        for p, x in xpts.items()]))  # fmt: skip
    if register:
        pit.register_run(db, run_id, 2026, [8], at, cutoff or at)


def test_post_deadline_forecast_is_never_reviewed():
    db = _db()
    _run(db, "pre", DEADLINE - timedelta(hours=3), {1: 4.0, 2: 4.0, 3: 3.0})
    _run(db, "post", DEADLINE + timedelta(days=2), {1: 6.0, 2: 2.0, 3: 0.0})  # "perfect"
    rep = review.prediction_review(db, 2026, 8)
    assert rep["run"]["run_id"] == "pre" and rep["eligible"]
    assert rep["mae"] == pytest.approx(2.0)  # |4-6| and |4-2|, not the post-match 0


def test_a_review_never_mixes_runs_per_player():
    db = _db()
    _run(db, "old", DEADLINE - timedelta(days=2), {1: 1.0, 2: 1.0, 3: 1.0})
    _run(db, "new", DEADLINE - timedelta(hours=2), {1: 5.0})  # newest run covers one player
    rep = review.prediction_review(db, 2026, 8)
    assert rep["run"]["run_id"] == "new" and rep["n"] == 1


def test_legacy_projections_are_judged_against_the_deadline_too():
    db = _db()
    _run(db, "pre", DEADLINE - timedelta(hours=3), {1: 4.0, 2: 4.0}, register=False)
    _run(db, "post", DEADLINE + timedelta(days=2), {1: 6.0, 2: 2.0}, register=False)
    rep = review.prediction_review(db, 2026, 8)
    assert rep["run"]["run_id"] == "pre" and rep["run"]["legacy"]


def test_no_deadline_means_no_review():
    db = _db()
    db.con.execute("DELETE FROM fpl_events")
    _run(db, "pre", DEADLINE - timedelta(hours=3), {1: 4.0}, register=False)
    rep = review.prediction_review(db, 2026, 8)
    assert not rep["available"]
    assert "deadline" in pit.eligible_run(db, 2026, 8).reason


def test_a_run_whose_cutoff_is_after_the_deadline_is_not_eligible():
    db = _db()
    _run(db, "late", DEADLINE - timedelta(hours=1), {1: 6.0}, cutoff=DEADLINE + timedelta(days=1))
    assert not pit.eligible_run(db, 2026, 8).found


def test_missing_results_stay_missing():
    db = _db()
    _run(db, "pre", DEADLINE - timedelta(hours=3), {1: 4.0, 2: 4.0, 3: 3.0})
    rep = review.prediction_review(db, 2026, 8)
    assert rep["n"] == 2 and rep["missing_actuals"] == 1  # player 3 is not scored as 0


def test_double_gameweek_components_come_from_both_fixtures():
    db = _db()
    db.con.execute("DELETE FROM fpl_points_gw WHERE player_id = 1")
    for fx, goals in ((80, 1), (81, 2)):
        db.append("fpl_points_gw", pd.DataFrame([{
            "season": 2026, "gw": 8, "fpl_id": 1, "player_id": 1, "fixture_id": fx, "minutes": 90,
            "total_points": 2 + 5 * goals,
            "explain": json.dumps([{"identifier": "minutes", "points": 2, "value": 90},
                                   {"identifier": "goals_scored", "points": 5 * goals,
                                    "value": goals}])}]))  # fmt: skip
    act = review._actuals(db, 2026, 8).set_index("player_id")
    comps = review._explain_components(act.loc[1, "explain"])
    assert act.loc[1, "actual"] == 19 and act.loc[1, "fixtures"] == 2
    assert comps == {"minutes": 4.0, "goals_scored": 15.0}


def test_provisional_reviews_are_flagged_and_never_calibrate():
    db = _db(final=True, checked=False)
    _run(db, "pre", DEADLINE - timedelta(hours=3), {1: 1.0, 2: 1.0})
    rep = review.prediction_review(db, 2026, 8)
    assert rep["available"] and not rep["final"]
    assert calibration.factors(db, 2026, 9) == {}
    db.con.execute("UPDATE fpl_events SET data_checked = true")
    rep = review.prediction_review(db, 2026, 8)
    assert rep["final"] and calibration.factors(db, 2026, 9)


def test_legacy_reviews_without_flags_do_not_calibrate():
    db = FootballDB()
    payload = {"by_position": {"MID": {"n": 500, "mean_pred": 3.0, "mean_actual": 4.0}}}
    db.con.execute("INSERT INTO reviews VALUES (2026, 3, now(), 'prediction', ?)",
                   [json.dumps(payload)])  # fmt: skip
    assert calibration.factors(db, 2026, 9) == {}


def test_finality_needs_finished_checked_and_points():
    db = _db(final=False)
    assert not pit.finality(db, 2026, 8).final
    db.con.execute("UPDATE fpl_events SET finished = true")
    assert pit.finality(db, 2026, 8).final
    assert not pit.finality(db, 2026, 9).final  # no event status at all


def test_events_are_recorded_from_the_bootstrap():
    db = FootballDB()
    pit.record_events(db, {"events": [{"id": 8, "deadline_time": "2026-10-23T17:30:00Z",
                                       "finished": True, "data_checked": False}]}, 2026)  # fmt: skip
    assert pit.deadline(db, 2026, 8) == pit.Deadline(DEADLINE, "fpl")
    assert pit.finality(db, 2026, 8).reason.startswith("gameweek finished but")


def test_deadline_is_estimated_from_the_first_kickoff_when_unrecorded():
    db = FootballDB()
    db.con.execute("INSERT INTO matches (match_id, competition_id, season, matchday, kickoff) "
                   "VALUES (1, 'ENG1', 2026, 8, TIMESTAMP '2026-10-23 19:00:00')")  # fmt: skip
    assert pit.deadline(db, 2026, 8) == pit.Deadline(DEADLINE, "estimated")


# ---- cutoffs through the projection inputs -------------------------------------------------------
def test_current_pool_and_market_odds_respect_the_cutoff():
    db = FootballDB()
    early, late = datetime(2026, 10, 22), datetime(2026, 10, 24)
    for at, status in ((early, "a"), (late, "i")):
        db.append("fpl_players_gw", pd.DataFrame([{"season": 2026, "gw": 8, "snapshot_at": at,
                                                   "fpl_id": 1, "player_id": 1, "team_id": 1,
                                                   "status": status, "raw": "{}"}]))  # fmt: skip
    assert project.current_pool(db, 2026, 8, as_of=DEADLINE)["status"].tolist() == ["a"]
    db.con.execute(
        "INSERT INTO matches (match_id, kickoff) VALUES (7, TIMESTAMP '2026-10-24 15:00')"
    )
    for at, closing, home in ((early, False, 2.0), (late, False, 1.2), (late, True, 1.1)):
        db.append("match_odds", pd.DataFrame([{"match_id": 7, "source": "x", "bookmaker": str(at),
                                               "closing": closing, "home": home, "draw": 3.5,
                                               "away": 4.0, "ingested_at": at}]))  # fmt: skip
    pre = project.market_lambdas(db, 7, as_of=DEADLINE)
    assert pre == project.market_lambdas(db, 7, as_of=DEADLINE, allow_backfill=False)
    assert pre[0] < project.market_lambdas(db, 7)[0]  # the later, shorter price is unseen
    assert (
        project.market_lambdas(db, 7, as_of=early - timedelta(days=1), allow_backfill=False) is None
    )


def test_closing_prices_are_after_the_deadline_even_in_a_backfill():
    db = FootballDB()
    db.con.execute(
        "INSERT INTO matches (match_id, kickoff) VALUES (7, TIMESTAMP '2025-10-24 15:00')"
    )
    loaded = datetime(2026, 10, 9)  # backfilled a year later
    db.append("match_odds", pd.DataFrame([{"match_id": 7, "source": "football-data",
                                           "bookmaker": "avg", "closing": True, "home": 1.5,
                                           "draw": 4.0, "away": 6.0, "ingested_at": loaded}]))  # fmt: skip
    assert project.market_lambdas(db, 7, as_of=datetime(2025, 10, 24, 13)) is None


def test_build_projection_passes_its_cutoff_to_the_pool():
    from fpl_agent.model.simulate import SimConfig
    from tests.synth import build_world

    w = build_world(n_teams=6, seasons=(2023, 2024), seed=5)
    cutoff = datetime(2024, 10, 1)
    w.db.con.execute(  # a post-cutoff snapshot that rules everyone out
        "INSERT INTO fpl_players_gw SELECT season, gw, TIMESTAMP '2024-10-05' AS snapshot_at, "
        "fpl_id, player_id, team_id, position, now_cost, selected_by_percent, 'n', 0, news, "
        "transfers_in_event, transfers_out_event, total_points, minutes, raw FROM fpl_players_gw "
        "WHERE season = 2024 AND gw = 8"
    )
    proj = project.build_projection(w.db, 2024, [9], as_of=date(2024, 10, 1), cutoff=cutoff,
                                    cfg=SimConfig(n=100, seed=1), pool_gw=8)  # fmt: skip
    assert (proj.meta["status"] == "a").all() and proj.cutoff == cutoff
    assert proj.mean().sum() > 0
