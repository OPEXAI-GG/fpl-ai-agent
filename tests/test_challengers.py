"""Shadow challengers: features use earlier gameweeks only, outcomes are exclusive, evidence is
measured across gameweeks, and the reports never claim more than the policy allows."""

import numpy as np
import pandas as pd
import pytest

from fpl_agent.model import challengers as ch
from tests.synth import build_world

FEATS = ch.ARMS["all features"]


@pytest.fixture(scope="module")
def world():
    return build_world(n_teams=8, seasons=(2023, 2024), seed=11)


@pytest.fixture(scope="module")
def pw(world):
    return ch.player_weeks(world.db, [2023, 2024])


def test_feature_table_has_one_row_per_player_week_and_every_block(pw):
    assert not pw.duplicated(["season", "gw", "player_id"]).any()
    assert set(FEATS) <= set(pw.columns)
    first = pw.sort_values(["season", "gw"]).groupby("player_id").head(1)
    assert first["pts_r5"].isna().all() and (first["weeks_seen"] == 0).all()  # no history yet
    assert pw["xg90"].notna().mean() > 0.9 and pw["opp_xga_r6"].notna().mean() > 0.5


def test_features_never_see_the_gameweek_they_predict(world, pw):
    """Rewriting one gameweek's results changes no feature of that gameweek or earlier ones."""
    db = world.db
    season, gw = 2024, 10
    db.con.execute(
        "UPDATE fpl_points_gw SET total_points = total_points + 50, minutes = 90, "
        "bps = bps + 99 WHERE season = ? AND gw = ?",
        [season, gw],
    )
    try:
        after = ch.player_weeks(db, [2023, 2024])
    finally:
        db.con.execute(
            "UPDATE fpl_points_gw SET total_points = total_points - 50, bps = bps - 99 "
            "WHERE season = ? AND gw = ?",
            [season, gw],
        )
    key = ["season", "gw", "player_id"]
    a = pw.set_index(key).sort_index()
    b = after.set_index(key).sort_index()
    upto = a.index.get_level_values("season") < season
    upto |= (a.index.get_level_values("season") == season) & (a.index.get_level_values("gw") <= gw)
    pd.testing.assert_frame_equal(a.loc[upto, FEATS], b.loc[upto, FEATS])
    later = (a.index.get_level_values("season") == season) & (
        a.index.get_level_values("gw") == gw + 1
    )
    assert not np.allclose(a.loc[later, "pts_r3"], b.loc[later, "pts_r3"])  # and later ones do


def test_minutes_outcomes_are_exclusive_and_probabilities_are_proper(pw):
    o = pw["outcome"].dropna().astype(int)
    assert set(o.unique()) <= {0, 1, 2, 3}
    zero = pw.loc[o.index, "minutes"] == 0
    assert (o[zero] == 0).all() and (o[~zero] != 0).all()
    p = ch.baseline_probs(pw.dropna(subset=["app_r5"]))
    assert np.allclose(p.sum(axis=1), 1) and (p > 0).all()
    m = ch.fit_minutes(pw[pw["season"] == 2023], FEATS)
    q = ch.class_probs(m, pw[pw["season"] == 2024].head(50), FEATS)
    assert q.shape == (50, 4) and np.allclose(q.sum(axis=1), 1)
    r = ch.fit_points(pw[pw["season"] == 2023], FEATS)
    assert np.isfinite(r.predict(pw[pw["season"] == 2024][FEATS].to_numpy(float))).all()


def test_uncertainty_is_across_gameweeks_not_player_rows():
    rng = np.random.default_rng(0)
    rows = []
    for gw in range(1, 6):  # each week has a shared shock: rows within a week are correlated
        shock = rng.normal(0, 1)
        for _ in range(200):
            rows.append({"season": 1, "gw": gw, "a": shock, "b": 0.0, "y": 0.0})
    df = pd.DataFrame(rows)
    b = ch.block(df, "a", "b")
    naive_se = (df["a"].abs()).std() / np.sqrt(len(df))
    assert b.weeks == 5 and len(b.per_week) == 5 and b.se > 5 * naive_se


def test_verdicts_follow_the_evidence_policy():
    few = ch.Block(-1.0, 0.1, 3, [-1, -1, -1])
    assert ch.verdict(few, "x").startswith("insufficient evidence")
    assert "lower by more than" in ch.verdict(ch.Block(-0.5, 0.1, 6, [0] * 6), "x")
    assert "higher by more than" in ch.verdict(ch.Block(0.5, 0.1, 6, [0] * 6), "x")
    assert ch.verdict(ch.Block(-0.1, 0.1, 6, [0] * 6), "x") == "no difference established"
    assert ch.verdict(ch.Block(None, None, 0, []), "x").startswith("insufficient")
