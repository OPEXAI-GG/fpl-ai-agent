"""Historical walk-forward of the workload feature: tuning kept apart from the final test, and
every test forecast made only from what was known at that gameweek's deadline."""

from datetime import datetime, timedelta

import numpy as np
import pandas as pd
import pytest

from fpl_agent import learning, pit
from tests.synth import build_world

SEASON, GW = 2024, 9


def test_walk_forward_keeps_tuning_and_test_apart():
    w = build_world(n_teams=6, seasons=(2023, 2024), seed=9)
    res = learning.walk_forward(w.db, 2024, list(range(4, 10)), bounds=(0.05, 0.1),
                                refit_every=3)  # fmt: skip
    assert res["available"]
    assert set(res["tune_gameweeks"]).isdisjoint(res["test_gameweeks"]) and res["test_gameweeks"]
    assert max(res["tune_gameweeks"]) < min(res["test_gameweeks"])  # chronological
    assert set(res["chosen"]) == {"all", "rest_only", "no_team_congestion"}
    assert res["test"]["baseline"]["n"] > 0 and 0 < res["test"]["all"]["brier"] < 1
    assert res["coverage"] > 0.5 and res["limitations"]
    again = learning.walk_forward(w.db, 2024, list(range(4, 10)), bounds=(0.05, 0.1),
                                  refit_every=3)  # fmt: skip
    assert again["test"] == res["test"]  # reproducible


@pytest.fixture
def dgw_world():
    """A world where team T plays twice in GW9: the second fixture three days after the first."""
    w = build_world(n_teams=6, seasons=(2023, 2024), seed=9)
    db = w.db
    first = db.query("SELECT * FROM matches WHERE season = ? AND matchday = ? ORDER BY match_id "
                     "LIMIT 1", [SEASON, GW]).iloc[0]  # fmt: skip
    t = int(first["home_team_id"])
    extra = first.to_dict()
    extra.update({"match_id": 990001, "kickoff":
                  first["kickoff"] + timedelta(days=3),
                  "match_date": (first["kickoff"] + timedelta(days=3)).date()})  # fmt: skip
    db.upsert("matches", pd.DataFrame([extra]))
    rows = db.query("SELECT * FROM fpl_points_gw WHERE season = ? AND gw = ? AND fixture_id = ?",
                    [SEASON, GW, int(first["match_id"])])  # fmt: skip
    snap = db.query("SELECT fpl_id, team_id FROM fpl_players_gw WHERE season = ? AND gw = ?",
                    [SEASON, GW])  # fmt: skip
    mine = set(snap[snap.team_id == t].fpl_id)
    second = rows[rows.fpl_id.isin(mine)].assign(fixture_id=990001,
                                                 kickoff=extra["kickoff"])  # fmt: skip
    db.append("fpl_points_gw", second)
    return w, t, int(first["match_id"])


def _predictions(db, monkeypatch):
    seen = []
    real = learning._scores

    def spy(p, y):
        seen.append(np.asarray(p, float).copy())
        return real(p, y)

    monkeypatch.setattr(learning, "_scores", spy)
    res = learning.walk_forward(db, SEASON, [GW], bounds=(0.1,), refit_every=1, tune_share=1.0)
    monkeypatch.setattr(learning, "_scores", real)
    assert res["available"]
    return seen[0]  # the baseline forecast of every test row of GW9


def test_post_deadline_outcomes_and_snapshots_cannot_change_a_gameweeks_forecast(
    dgw_world, monkeypatch
):
    w, team, first_match = dgw_world
    db = w.db
    before = _predictions(db, monkeypatch)
    # the first fixture's results (known only after the deadline) are rewritten ...
    db.con.execute("UPDATE fpl_points_gw SET minutes = CASE WHEN minutes > 0 THEN 0 ELSE 90 END, "
                   "total_points = 0 WHERE season = ? AND gw = ? AND fixture_id = ?",
                   [SEASON, GW, first_match])  # fmt: skip
    # ... and a squad snapshot taken after the deadline changes prices and statuses
    dl = pit.deadline(db, SEASON, GW).at
    late = db.query("SELECT * FROM fpl_players_gw WHERE season = ? AND gw = ?", [SEASON, GW])
    late = late.assign(snapshot_at=dl + timedelta(hours=6), now_cost=late["now_cost"] + 15,
                       status="i", chance_next=0)  # fmt: skip
    db.upsert("fpl_players_gw", late)
    after = _predictions(db, monkeypatch)
    assert before.shape == after.shape
    np.testing.assert_array_equal(before, after)  # including the later DGW fixture


def test_both_fixtures_of_a_double_gameweek_get_the_deadline_forecast(dgw_world, monkeypatch):
    w, team, _ = dgw_world
    rows = learning.walk_forward_rows(w.db, SEASON, GW)
    two = rows[rows.groupby("player_id")["fixture_id"].transform("nunique") == 2]
    assert len(two) and (two.groupby("player_id")["p_baseline"].nunique() == 1).all()
    assert (two["kickoff"] > pit.deadline(w.db, SEASON, GW).at).all()
    assert isinstance(rows["cutoff"].iloc[0], datetime | pd.Timestamp)
