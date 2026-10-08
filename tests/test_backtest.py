import numpy as np
import pytest

from fpl_agent.model import backtest
from fpl_agent.model.simulate import SimConfig
from tests.synth import build_world


@pytest.fixture(scope="module")
def results():
    w = build_world(n_teams=10, seasons=(2022, 2023, 2024), seed=41)
    return backtest.projection_backtest(
        w.db, 2024, list(range(4, 14)), SimConfig(n=500), refit_every=5
    )


def test_backtest_scores_every_position(results):
    assert set(results.position) == {"GKP", "DEF", "MID", "FWD"}
    assert results.gw.nunique() == 10 and results.actual.notna().all()
    assert results.sim.notna().all()


def test_simulator_beats_the_last_five_baseline_in_a_world_it_understands(results):
    summary = backtest.summarize(results)
    assert {"sim", "last5"} <= set(summary.predictor)
    verdict = backtest.beats_baselines(summary)
    assert verdict["ALL:last5"] and sum(verdict.values()) >= 3


def test_no_look_ahead_actuals_never_enter_the_projection():
    w = build_world(n_teams=8, seasons=(2023, 2024), seed=42)
    a = backtest.projection_backtest(w.db, 2024, [8], SimConfig(n=300))
    # corrupt the target gameweek's actual points; projections must not move
    w.db.con.execute("UPDATE fpl_points_gw SET total_points = total_points + 50, minutes = 90 "
                     "WHERE season = 2024 AND gw = 8")  # fmt: skip
    b = backtest.projection_backtest(w.db, 2024, [8], SimConfig(n=300))
    assert np.allclose(
        a.sort_values("player_id").sim.to_numpy(), b.sort_values("player_id").sim.to_numpy()
    )
    assert not np.allclose(
        a.sort_values("player_id").actual.to_numpy(), b.sort_values("player_id").actual.to_numpy()
    )


def test_summary_columns_and_small_samples_are_skipped():
    import pandas as pd

    tiny = pd.DataFrame({"position": ["MID"] * 5, "actual": range(5), "sim": range(5),
                         "last5": range(5), "fpl_xp": [np.nan] * 5})  # fmt: skip
    assert backtest.summarize(tiny).empty
