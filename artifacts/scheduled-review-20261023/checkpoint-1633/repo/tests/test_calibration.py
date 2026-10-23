"""Learning loop: reviews of finished gameweeks nudge later projections, boundedly."""

import json

import numpy as np
import pandas as pd

from fpl_agent.db.database import FootballDB
from fpl_agent.model import calibration
from fpl_agent.model.project import Projection


def add_review(db, gw, pos="MID", pred=3.0, actual=4.0, n=500, season=2026):
    payload = {"by_position": {pos: {"n": n, "mean_pred": pred, "mean_actual": actual}}}
    db.con.execute(
        "INSERT INTO reviews VALUES (?, ?, now(), 'prediction', ?)",
        [season, gw, json.dumps(payload)],
    )


def test_no_reviews_means_no_change():
    assert calibration.factors(FootballDB(), 2026, 10) == {}


def test_underprediction_raises_the_factor_but_is_shrunk_and_capped():
    db = FootballDB()
    add_review(db, 1, actual=4.0, n=500)  # 33% under-prediction on little data
    f = calibration.factors(db, 2026, 5)["MID"]
    assert 1.0 < f < 1.05  # shrunk: 500 of 4500 evidence weight
    for gw in range(2, 40):
        add_review(db, gw, actual=6.0, n=500)  # absurd, repeated
    assert calibration.factors(db, 2026, 99)["MID"] == 1.1  # capped


def test_only_finished_gameweeks_are_used():
    db = FootballDB()
    add_review(db, 8, actual=2.0)
    assert calibration.factors(db, 2026, 8) == {}  # gw 8 itself is not yet "before" gw 8
    assert calibration.factors(db, 2026, 9)["MID"] < 1.0


def test_apply_scales_only_the_affected_positions():
    meta = pd.DataFrame({"player_id": [1, 2], "position": ["MID", "DEF"]})
    pts = np.ones((2, 1, 4), dtype=np.float32)
    proj = Projection("r", 2026, [10], meta, pts, np.zeros((2, 1, 4), dtype=np.int16))
    calibration.apply(proj, {"MID": 1.1})
    assert np.allclose(proj.points[0], 1.1) and np.allclose(proj.points[1], 1.0)
