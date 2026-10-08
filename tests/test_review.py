"""Review loop: prediction error by position/component and decision quality vs hindsight."""

import json
from datetime import date

import pytest

from fpl_agent import review
from fpl_agent.model import project
from fpl_agent.model.simulate import SimConfig
from tests.synth import build_world


@pytest.fixture(scope="module")
def stored():
    w = build_world(n_teams=10, seasons=(2022, 2023, 2024), seed=31)
    proj = project.build_projection(
        w.db, 2024, [10, 11], as_of=date(2024, 10, 20), cfg=SimConfig(n=300, seed=1), pool_gw=9)
    project.save_projection(w.db, proj)
    return w, proj


def test_prediction_review_reports_error_by_position(stored):
    w, _ = stored
    rep = review.prediction_review(w.db, 2024, 10)
    assert rep["available"] and rep["n"] > 50
    assert set(rep["by_position"]) <= {"GKP", "DEF", "MID", "FWD"}
    assert rep["rmse"] > 0 and abs(rep["bias"]) < 3
    assert rep["top20"]["predicted"] > rep["top20"]["actual"] - 5
    assert w.db.con.execute("SELECT count(*) FROM reviews WHERE kind='prediction'").fetchone()[0] == 1


def test_review_is_idempotent_per_gameweek(stored):
    w, _ = stored
    review.prediction_review(w.db, 2024, 10)
    review.prediction_review(w.db, 2024, 10)
    assert w.db.con.execute("SELECT count(*) FROM reviews WHERE gw=10 AND kind='prediction'").fetchone()[0] == 1


def test_components_are_compared_with_fpl_explain(stored):
    w, proj = stored
    pid = int(proj.player_ids[0])
    w.db.con.execute(
        "UPDATE fpl_points_gw SET explain = ? WHERE season = 2024 AND gw = 10 AND player_id = ?",
        [json.dumps([{"identifier": "minutes", "points": 2, "value": 90}]), pid])
    rep = review.prediction_review(w.db, 2024, 10)
    assert "minutes" in rep["components"] and "goals_scored" in rep["components"]


def test_missing_data_is_reported_not_raised(stored):
    w, _ = stored
    assert review.prediction_review(w.db, 2024, 30) == {"gw": 30, "available": False}
    assert review.decision_review(w.db, 2024, 30) == {"gw": 30, "available": False}


def test_decision_review_scores_captain_and_transfers(stored):
    w, proj = stored
    ids = [int(p) for p in proj.player_ids[:15]]
    payload = {"gw": 10, "action": "make transfers", "chip": None, "transfers_in": [ids[0]],
               "transfers_out": [ids[1]], "hits": 1, "captain": ids[2], "vice": ids[3],
               "xi": ids[:11], "bench": ids[11:15], "xp": 55.0, "xp_no_transfers": 53.0}
    w.db.con.execute("INSERT INTO decisions VALUES (2024, 10, now(), 'dry', ?)", [json.dumps(payload)])
    rep = review.decision_review(w.db, 2024, 10)
    assert rep["available"] and rep["captain_regret"] >= 0
    assert rep["points_after_hits"] == rep["xi_points"] - 4
    assert rep["transfer_gain_after_hits"] == rep["transfer_gain"] - 4
    s = review.summarize(w.db, 2024)
    assert s["weeks_reviewed"] >= 1 and s["total_points"] == rep["points_after_hits"]
