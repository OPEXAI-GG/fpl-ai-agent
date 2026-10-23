import numpy as np

from fpl_agent.solver.value import differential_watchlist
from tests.test_decide import projection


def test_missing_ownership_is_not_a_differential():
    proj, _ = projection()
    assert differential_watchlist(proj) == []
    proj.meta["owned"] = np.nan
    assert differential_watchlist(proj) == []


def test_watchlist_requires_minutes_value_and_observed_ownership():
    proj, _ = projection()
    proj.meta["owned"] = 50.0
    proj.meta["price"] = 50
    i = next(i for i, r in enumerate(proj.meta.itertuples()) if r.position == "MID")
    proj.meta.loc[i, "owned"] = 2.5
    proj.points[i] = 9.0
    rows = differential_watchlist(proj)
    assert len(rows) == 1 and rows[0]["player_id"] == proj.player_ids[i]
    assert rows[0]["peer_edge"] > 0
    assert "prior" in rows[0]["evidence"]
    before = proj.points.copy()
    proj.minutes[i] = 30
    assert differential_watchlist(proj) == []
    assert np.array_equal(proj.points, before)  # ownership never inflates projected points
