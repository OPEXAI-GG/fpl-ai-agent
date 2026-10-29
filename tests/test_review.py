"""Review loop: prediction error by position/component and decision quality vs hindsight."""

import json
from datetime import date, timedelta

import pytest

from fpl_agent import review
from fpl_agent.db import gameweeks
from fpl_agent.model import project
from fpl_agent.model.simulate import SimConfig
from tests.synth import build_world


@pytest.fixture(scope="module")
def stored():
    w = build_world(n_teams=10, seasons=(2022, 2023, 2024), seed=31)
    proj = project.build_projection(
        w.db, 2024, [10, 11], as_of=date(2024, 10, 20), cfg=SimConfig(n=300, seed=1), pool_gw=9
    )
    project.save_projection(w.db, proj)
    return w, proj


def test_prediction_review_reports_error_by_position(stored):
    w, _ = stored
    rep = review.prediction_review(w.db, 2024, 10)
    assert rep["available"] and rep["n"] > 50
    assert set(rep["by_position"]) <= {"GKP", "DEF", "MID", "FWD"}
    assert rep["rmse"] > 0 and abs(rep["bias"]) < 3
    assert rep["top20"]["predicted"] > rep["top20"]["actual"] - 5
    assert (
        w.db.con.execute("SELECT count(*) FROM reviews WHERE kind='prediction'").fetchone()[0] == 1
    )


def test_review_is_idempotent_per_gameweek(stored):
    w, _ = stored
    review.prediction_review(w.db, 2024, 10)
    review.prediction_review(w.db, 2024, 10)
    assert (
        w.db.con.execute(
            "SELECT count(*) FROM reviews WHERE gw=10 AND kind='prediction'"
        ).fetchone()[0]
        == 1
    )


def test_components_are_compared_with_fpl_explain(stored):
    w, proj = stored
    pid = int(proj.player_ids[0])
    w.db.con.execute(
        "UPDATE fpl_points_gw SET explain = ? WHERE season = 2024 AND gw = 10 AND player_id = ?",
        [json.dumps([{"identifier": "minutes", "points": 2, "value": 90}]), pid],
    )
    rep = review.prediction_review(w.db, 2024, 10)
    assert "minutes" in rep["components"] and "goals_scored" in rep["components"]


def test_missing_data_is_reported_not_raised(stored):
    w, _ = stored
    assert review.prediction_review(w.db, 2024, 30) == {"gw": 30, "available": False}
    assert review.decision_review(w.db, 2024, 30) == {"gw": 30, "available": False}


def test_decision_review_scores_captain_and_transfers(stored):
    w, proj = stored
    ids = [int(p) for p in proj.player_ids[:15]]
    payload = {
        "gw": 10,
        "action": "make transfers",
        "chip": None,
        "transfers_in": [ids[0]],
        "transfers_out": [ids[1]],
        "hits": 1,
        "captain": ids[2],
        "vice": ids[3],
        "xi": ids[:11],
        "bench": ids[11:15],
        "xp": 55.0,
        "xp_no_transfers": 53.0,
    }
    w.db.con.execute(
        "INSERT INTO decisions VALUES (2024, 10, now(), 'dry', ?)", [json.dumps(payload)]
    )
    rep = review.decision_review(w.db, 2024, 10)
    assert rep["available"] and rep["captain_regret"] >= 0
    assert rep["points_after_hits"] == rep["xi_points"] - 4
    assert rep["transfer_gain_after_hits"] == rep["transfer_gain"] - 4
    s = review.summarize(w.db, 2024)
    assert s["weeks_reviewed"] >= 1 and s["total_points"] == rep["points_after_hits"]


def test_prediction_review_scores_the_field_core_separately(stored):
    w, proj = stored
    core = [int(p) for p in proj.player_ids[:5]]
    fpl = w.db.query("SELECT DISTINCT fpl_id, player_id FROM fpl_players_gw WHERE season = 2024")
    rows = fpl[fpl["player_id"].isin(core)]
    w.db.upsert("field_ownership", rows[["fpl_id"]].assign(season=2024, gw=10, owned=80.0,
                                                          eo=90.0, sample=200))  # fmt: skip
    try:
        rep = review.prediction_review(w.db, 2024, 10)
    finally:
        w.db.con.execute("DELETE FROM field_ownership")
    assert rep["template"]["n"] == len(core) and rep["template"]["mean_pred"] > 0


def test_only_a_pre_deadline_forecast_is_eligible_and_missing_results_are_not_zero():
    """Audit cases: a forecast saved after the deadline must not feed calibration, and a
    projected player without an official result must not be scored as 0 points."""
    w = build_world(n_teams=8, seasons=(2023, 2024), seed=33)
    proj = project.build_projection(
        w.db, 2024, [10], as_of=date(2024, 10, 20), cfg=SimConfig(n=200, seed=1), pool_gw=9
    )
    project.save_projection(w.db, proj)  # saved now: long after the 2024 deadline
    first = w.db.con.execute(
        "SELECT min(kickoff) FROM matches WHERE season = 2024 AND matchday = 10"
    ).fetchone()[0]
    cut = first - timedelta(minutes=90)
    gameweeks.log_deadlines(w.db, [{"id": 10, "deadline_time": cut.isoformat() + "Z"}], 2024, cut)
    rep = review.prediction_review(w.db, 2024, 10)
    assert rep["available"] and rep["pre_deadline"] is False
    assert rep["eligible_for_calibration"] is False and rep["n_missing_outcome"] == 0
    # the same forecast stamped before the deadline is the one judged, and is eligible
    w.db.con.execute("UPDATE projections SET created_at = ? - INTERVAL 1 HOUR WHERE run_id = ?",
                     [cut, proj.run_id])  # fmt: skip
    rep = review.prediction_review(w.db, 2024, 10)
    assert rep["eligible_for_calibration"] is True
    assert rep["projection_choice"] == "newest run before the deadline (official deadline)"
    # results missing for some projected players: they leave the sample, they are not zeros
    gone = [int(p) for p in proj.meta["player_id"][:5]]
    w.db.con.execute(
        f"DELETE FROM fpl_points_gw WHERE season = 2024 AND gw = 10 "
        f"AND player_id IN ({', '.join(map(str, gone))})"
    )
    after = review.prediction_review(w.db, 2024, 10)
    assert after["n_missing_outcome"] == 5 and after["n"] == rep["n"] - 5


def test_projection_choice_names_the_approximate_deadline_when_no_official_one(monkeypatch):
    """Without a stored official deadline the cutoff is first kickoff - 90 min, and the label
    says so (it used to say that even when the official deadline was used)."""
    from datetime import datetime

    w = build_world(n_teams=8, seasons=(2023, 2024), seed=33)
    proj = project.build_projection(
        w.db, 2024, [10], as_of=date(2024, 10, 20), cfg=SimConfig(n=200, seed=1), pool_gw=9
    )
    project.save_projection(w.db, proj)
    monkeypatch.setattr(review, "_cutoff", lambda db, season, gw: datetime(2100, 1, 1))
    run, chosen = review.projection_run(w.db, 2024, 10)
    assert run == proj.run_id
    assert chosen == "newest run before the deadline (first kickoff - 90 min)"
    assert review.prediction_review(w.db, 2024, 10)["eligible_for_calibration"] is True
