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
    verdict = backtest.beats_baselines(results)
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


def test_a_baseline_missing_on_hard_rows_is_compared_on_common_rows_only():
    """Audit case: the simulator looks better overall (RMSE 1.414 vs 1.8) only because the
    baseline skipped rows; on the rows both predicted it loses (2.0 vs 1.8)."""
    import pandas as pd

    n = 40
    actual = np.arange(n) % 4.0
    sim = actual + np.r_[np.full(20, 2.0), np.zeros(20)]  # off by 2 on the first half only
    last5 = actual + np.r_[np.full(20, 1.8), np.full(20, np.nan)]  # first half only
    rows = pd.DataFrame({"position": "MID", "actual": actual, "sim": sim, "last5": last5,
                         "fpl_xp": np.nan})  # fmt: skip
    s = backtest.summarize(rows)
    unpaired = s[s.position == "MID"].set_index("predictor")["rmse"]
    assert unpaired["sim"] < unpaired["last5"]  # the misleading aggregate view
    cmp = backtest.paired_comparison(rows).set_index(["position", "baseline"])
    assert cmp.loc[("MID", "last5"), "n_common"] == 20
    assert cmp.loc[("MID", "last5"), "n_sim_only"] == 20
    assert cmp.loc[("MID", "last5"), "sim"] == pytest.approx(2.0)
    assert backtest.beats_baselines(rows) == {"MID:last5": False, "ALL:last5": False}
    with pytest.raises(TypeError):
        backtest.beats_baselines(backtest.summarize(rows))
