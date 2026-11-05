"""Feature slice end to end: the workload overlay -> changed minutes and points -> saved detail
and report line; workload features keep unknown apart from zero and stay bounded; flags default
to shadow and the environment wins."""

import json
from datetime import datetime

import numpy as np
import pandas as pd
import pytest

from fpl_agent import agent, pit
from fpl_agent.model import overlays, project, workload
from fpl_agent.model.simulate import SimConfig
from tests.synth import build_world

CUTOFF = datetime(2024, 10, 1)  # the GW9 run's information cutoff (GW9 kicks off 5 Oct)


@pytest.fixture(scope="module")
def world():
    return build_world(n_teams=6, seasons=(2023, 2024), seed=5)


@pytest.fixture(scope="module")
def base(world):
    return project.build_projection(world.db, 2024, [9, 10], as_of=CUTOFF.date(), cutoff=CUTOFF,
                                    cfg=SimConfig(n=400, seed=3), pool_gw=8)  # fmt: skip


def _with_workload(world, base, **kw):
    ov = overlays.Workload(world.db, **kw)
    inp = base.inputs
    proj = project.build_projection(
        world.db, 2024, [9, 10], as_of=CUTOFF.date(), cutoff=CUTOFF, cfg=SimConfig(n=400, seed=3),
        pool_gw=8, overlays=[ov], minutes_model=inp["minutes_model"], ratings=inp["ratings"],
        frame=inp["frame"], pm=inp["pm"])  # fmt: skip
    return proj, ov


def test_workload_changes_minutes_points_the_saved_detail_and_the_report(world, base):
    proj, ov = _with_workload(world, base)
    assert ov.changed > 0
    a = proj.minutes_table.set_index(["player_id", "gw"]).xs(9, level="gw")
    b = base.minutes_table.set_index(["player_id", "gw"]).xs(9, level="gw")
    moved = a.index[(a.p_start - b.p_start).abs() > 1e-6]
    assert len(moved) == ov.changed
    xa = proj.frame().set_index(["player_id", "gw"]).xs(9, level="gw")["xpts"]
    xb = base.frame().set_index(["player_id", "gw"]).xs(9, level="gw")["xpts"]
    assert (xa.loc[moved] - xb.loc[moved]).abs().max() > 0  # the points moved too
    lines = agent.explain_overlays([ov], proj, {}, shadow=True)
    assert lines and "shadow, not used by the plan" in lines[0]
    project.save_projection(world.db, proj, created_at=CUTOFF)
    p = pit.run_projection(world.db, proj.run_id, 2024, 9)
    det = {int(r.player_id): r.detail for r in p.itertuples()}
    saved = {pid for pid, d in det.items() if json.loads(d).get("workload")}
    assert saved == {int(x) for x in moved}  # every change, and only those, is recorded


# ---- workload -----------------------------------------------------------------------------------
def test_workload_features_units_cutoff_and_unknown():
    apps = pd.DataFrame({"player_id": [1, 1, 1], "match_id": [1, 2, 3], "minutes": [90, 0, 75],
                         "kickoff": pd.to_datetime(["2024-09-20 15:00", "2024-09-25 19:00",
                                                    "2024-09-28 15:00"])})  # fmt: skip
    teams = pd.DataFrame({"team_id": [7, 7, 7], "kickoff": apps["kickoff"]})
    targets = pd.DataFrame({"player_id": [1, 2], "team_id": [7, 8],
                            "kickoff": pd.to_datetime(["2024-10-01 19:00"] * 2)})  # fmt: skip
    f = workload.workload_features(targets, apps, teams)
    assert f.loc[0, "xc_rest_days"] == pytest.approx(3 + 4 / 24)
    assert f.loc[0, "xc_mins_7d"] == 75 and f.loc[0, "xc_mins_14d"] == 165
    assert f.loc[0, "xc_team_matches_7d"] == 2
    assert f.loc[1].isna().all()  # no coverage at all: unknown, not zero
    # facts after the cutoff (minus the 3h for results to be public) do not exist yet
    g = workload.workload_features(targets.iloc[:1], apps, teams, cutoff=datetime(2024, 9, 28, 17))
    assert g.loc[0, "xc_mins_7d"] == 0 and g.loc[0, "xc_rest_days"] == pytest.approx(11 + 4 / 24)


def test_covered_player_with_no_minutes_is_zero_not_unknown():
    apps = pd.DataFrame({"player_id": [1], "match_id": [1], "minutes": [0],
                         "kickoff": pd.to_datetime(["2024-09-28 15:00"])})  # fmt: skip
    targets = pd.DataFrame({"player_id": [1], "team_id": [7],
                            "kickoff": pd.to_datetime(["2024-10-01 19:00"])})  # fmt: skip
    f = workload.workload_features(targets, apps, pd.DataFrame(columns=["team_id", "kickoff"]))
    assert f.loc[0, "xc_mins_7d"] == 0 and np.isnan(f.loc[0, "xc_rest_days"])


def test_workload_overlay_is_bounded_and_next_gameweek_only(world, base):
    ov = overlays.Workload(world.db, bound=0.05)
    inp = base.inputs
    proj = project.build_projection(
        world.db, 2024, [9, 10], as_of=CUTOFF.date(), cutoff=CUTOFF, cfg=SimConfig(n=200, seed=3),
        pool_gw=8, overlays=[ov], minutes_model=inp["minutes_model"], ratings=inp["ratings"],
        frame=inp["frame"], pm=inp["pm"])  # fmt: skip
    a = proj.minutes_table.set_index(["player_id", "gw"])
    b = base.minutes_table.set_index(["player_id", "gw"])
    d9 = (a.xs(9, level="gw").p_start - b.xs(9, level="gw").p_start).abs()
    assert d9.max() <= 0.05 + 1e-9 and ov.changed > 0
    assert np.allclose(a.xs(10, level="gw").p_start, b.xs(10, level="gw").p_start)
    assert ov.summary()["train_rows"] > 0


def test_flags_default_to_shadow_and_env_wins(monkeypatch):
    monkeypatch.delenv("FEATURE_WORKLOAD", raising=False)
    assert overlays.modes() == {"workload": "shadow"}
    assert overlays.modes({"workload": "on"}) == {"workload": "on"}
    monkeypatch.setenv("FEATURE_WORKLOAD", "off")
    assert overlays.modes({"workload": "on"}) == {"workload": "off"}
    monkeypatch.setenv("FEATURE_WORKLOAD", "bogus")
    assert overlays.modes() == {"workload": "shadow"}
