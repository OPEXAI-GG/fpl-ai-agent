"""End to end: database -> projections, on the synthetic world with known truth."""

from datetime import date

import numpy as np
import pandas as pd
import pytest

from fpl_agent.model import features, project
from fpl_agent.model.simulate import SimConfig
from tests.synth import build_world


@pytest.fixture(scope="module")
def world():
    return build_world(n_teams=10, seasons=(2022, 2023, 2024), seed=31)


@pytest.fixture(scope="module")
def proj(world):
    return project.build_projection(
        world.db, 2024, [10, 11, 12], as_of=date(2024, 10, 20), cfg=SimConfig(n=600, seed=1),
        pool_gw=9,
    )


def test_projection_shape_and_probabilities(world, proj):
    p, g, n = proj.points.shape
    assert g == 3 and n == 600 and p == len(world.players)
    f = proj.frame()
    assert f.xpts.between(-1, 20).all()
    assert f.p_zero.between(0, 1).all() and (f.q90 >= f.q10).all()
    assert f.groupby("gw").size().eq(p).all()


def test_goalkeepers_defenders_midfielders_forwards_all_have_sensible_means(proj):
    f = proj.frame()
    by_pos = f.groupby("position").xpts.mean()
    assert by_pos.between(0.5, 5).all()
    # first-choice players score more than rotation players
    starters = f[f.exp_minutes > 60].xpts.mean()
    bench = f[f.exp_minutes < 15].xpts.mean()
    assert starters > bench * 2


def test_projections_follow_the_truth_of_the_synthetic_world(world, proj):
    f = proj.frame().groupby("player_id").xpts.mean()
    truth = pd.Series({p: t["start_p"] * (1 + t["goal_w"]) for p, t in world.players.items()})
    assert np.corrcoef(f.loc[truth.index], truth)[0, 1] > 0.6


def test_a_team_with_no_fixture_in_the_gameweek_scores_zero():
    w = build_world(n_teams=8, seasons=(2023, 2024), seed=3)
    # remove one team's gw 5 fixture to create a blank
    team = list(w.teams)[0]
    w.db.con.execute(
        "UPDATE matches SET matchday = NULL WHERE season = 2024 AND matchday = 5 AND "
        "(home_team_id = ? OR away_team_id = ?)", [team, team])  # fmt: skip
    pr = project.build_projection(w.db, 2024, [5], as_of=date(2024, 9, 20), cfg=SimConfig(n=200),
                                  pool_gw=4)  # fmt: skip
    ids = {p for p, t in w.players.items() if t["team"] == team}
    assert not ids & set(pr.player_ids)  # not in the pool of that gameweek


def test_double_gameweek_roughly_doubles_expected_points():
    w = build_world(n_teams=8, seasons=(2023, 2024), seed=4)
    # make GW 6 fixtures of one team also appear in GW 5 (a double)
    team = list(w.teams)[1]
    w.db.con.execute(
        "UPDATE matches SET matchday = 5 WHERE season = 2024 AND matchday = 6 AND "
        "(home_team_id = ? OR away_team_id = ?)", [team, team])  # fmt: skip
    pr = project.build_projection(w.db, 2024, [5], as_of=date(2024, 9, 20), cfg=SimConfig(n=800),
                                  pool_gw=4)  # fmt: skip
    single = project.build_projection(w.db, 2024, [4], as_of=date(2024, 9, 20), cfg=SimConfig(n=800),
                                      pool_gw=4)  # fmt: skip
    mine = [p for p, t in w.players.items() if t["team"] == team]
    d = pr.frame().set_index("player_id").loc[mine, "xpts"].sum()
    s = single.frame().set_index("player_id").loc[mine, "xpts"].sum()
    assert d > 1.5 * s


def test_odds_blending_moves_the_projection():
    w = build_world(n_teams=8, seasons=(2023, 2024), seed=5)
    base = project.build_projection(w.db, 2024, [8], as_of=date(2024, 10, 1), cfg=SimConfig(n=500),
                                    pool_gw=7)  # fmt: skip
    fx = project.fixtures_for(w.db, 2024, [8])
    for r in fx.itertuples():  # the market thinks every home side is a heavy favourite
        w.db.con.execute(
            "INSERT INTO match_odds (match_id, source, bookmaker, closing, home, draw, away, "
            "over25, under25) VALUES (?, 'test', 'avg', TRUE, 1.3, 6.0, 12.0, 1.5, 2.6)",
            [r.match_id])  # fmt: skip
    assert project.market_lambdas(w.db, int(fx.match_id[0]))[0] > 2.0
    blended = project.build_projection(w.db, 2024, [8], as_of=date(2024, 10, 1), cfg=SimConfig(n=500),
                                       pool_gw=7, odds_weight=1.0)  # fmt: skip
    homes = set(fx.home)
    fm = blended.meta.team_id.isin(homes).to_numpy()
    assert blended.mean()[fm].sum() > base.mean()[fm].sum()


def test_projection_is_deterministic_for_a_seed(world):
    a = project.build_projection(world.db, 2024, [12], as_of=date(2024, 10, 20),
                                 cfg=SimConfig(n=300, seed=5), pool_gw=11)  # fmt: skip
    b = project.build_projection(world.db, 2024, [12], as_of=date(2024, 10, 20),
                                 cfg=SimConfig(n=300, seed=5), pool_gw=11)  # fmt: skip
    assert np.array_equal(a.points, b.points)


def test_save_and_reload_projection(world, proj):
    db = world.db
    n = project.save_projection(db, proj)
    got = project.load_projection(db, proj.run_id)
    assert n == len(got) == len(proj.frame())
    assert got.xpts.sum() == pytest.approx(proj.frame().xpts.sum(), rel=1e-6)


def test_bps_noise_is_estimated_from_history(world):
    frame = features.player_gw_frame(world.db)
    from fpl_agent.rules import Rules

    noise = project.fit_bps_noise(frame, Rules.load(2026))
    assert set(noise.mu90) == {"GKP", "DEF", "MID", "FWD"}
    tiny = project.fit_bps_noise(frame.head(50), Rules.load(2026))
    assert tiny.mu90 == project.BpsNoise().mu90  # too little data: defaults


def test_current_pool_respects_as_of(world):
    all_rows = project.current_pool(world.db, 2024, 5)
    early = project.current_pool(world.db, 2024, 5, as_of=pd.Timestamp("2000-01-01"))
    assert len(all_rows) == len(world.players) and early.empty
