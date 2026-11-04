"""Historical walk-forward of the workload feature: tuning kept apart from the final test, and
every test forecast made only from what was known at that gameweek's deadline."""

import json
from datetime import datetime, timedelta

import numpy as np
import pandas as pd
import pytest

from fpl_agent import learning, pit
from fpl_agent.model import overlays, project, workload
from fpl_agent.model.simulate import SimConfig
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


# ---- refreshed inference history between refits ---------------------------------------------
def _with_outcome(db, gw, pids, minutes, starts):
    """Rewrite the official results of `pids` in `gw` (minutes and FPL's `starts`)."""
    rows = db.query("SELECT * FROM fpl_points_gw WHERE season = ? AND gw = ?", [SEASON, gw])
    hit = rows["player_id"].isin(pids)
    for i in rows.index[hit]:
        stats = json.loads(rows.at[i, "stats"]) if rows.at[i, "stats"] else {}
        stats["starts"] = starts
        rows.at[i, "stats"] = json.dumps(stats)
        rows.at[i, "minutes"] = minutes
    db.con.execute("DELETE FROM fpl_points_gw WHERE season = ? AND gw = ?", [SEASON, gw])
    db.append("fpl_points_gw", rows)


def test_a_result_known_by_the_deadline_reaches_inference_without_a_refit():
    w = build_world(n_teams=6, seasons=(2023, 2024), seed=9)
    db = w.db
    apps, teams = workload.appearance_log(db), workload.team_log(db)
    models = learning._wf_models(db, SEASON, 6, learning.WF_DEFAULT_ABLATIONS, apps, teams)
    assert models["fitted_for"] == 6  # GW9 reuses the GW6 fit (refit_every=4)
    before = learning.walk_forward_rows(db, SEASON, GW, models, apps=apps, teams=teams)
    gw8 = db.query("SELECT player_id, minutes FROM fpl_points_gw WHERE season = ? AND gw = 8",
                   [SEASON])  # fmt: skip
    regulars = set(gw8[gw8.minutes >= 80].player_id) & set(before.player_id)
    benched = sorted(regulars)[:10]
    _with_outcome(db, 8, benched, minutes=0, starts=0)  # GW8 is final before GW9's deadline
    after = learning.walk_forward_rows(db, SEASON, GW, models, apps=apps, teams=teams)
    a = before.set_index(["player_id", "fixture_id"])["p_baseline"]
    b = after.set_index(["player_id", "fixture_id"])["p_baseline"]
    moved = (a - b.reindex(a.index)).abs() > 1e-9
    assert set(a.index[moved].get_level_values(0)) >= set(benched)  # the new GW8 result counts
    assert models["fitted_for"] == 6  # with the same fitted estimators


def test_post_deadline_data_cannot_change_a_forecast_made_between_refits():
    w = build_world(n_teams=6, seasons=(2023, 2024), seed=9)
    db = w.db
    apps, teams = workload.appearance_log(db), workload.team_log(db)
    models = learning._wf_models(db, SEASON, 6, learning.WF_DEFAULT_ABLATIONS, apps, teams)
    cols = ["p_baseline", *[f"p_{n}" for n in learning.WF_DEFAULT_ABLATIONS]]
    before = learning.walk_forward_rows(db, SEASON, GW, models, apps=apps, teams=teams)
    pids = sorted(set(before.player_id))[:15]
    _with_outcome(db, GW, pids, minutes=0, starts=0)  # GW9's own results
    dl = pit.deadline(db, SEASON, GW).at
    late = db.query("SELECT * FROM fpl_players_gw WHERE season = ? AND gw = ?", [SEASON, GW])
    db.upsert("fpl_players_gw", late.assign(snapshot_at=dl + timedelta(hours=6), status="i",
                                            chance_next=0))  # fmt: skip
    after = learning.walk_forward_rows(db, SEASON, GW, models, apps=apps, teams=teams)
    key = ["player_id", "fixture_id"]
    a, b = before.set_index(key)[cols], after.set_index(key)[cols]
    np.testing.assert_array_equal(a.to_numpy(), b.reindex(a.index).to_numpy())


# ---- one fixture time per club, the same at runtime and in evaluation ------------------------
@pytest.fixture
def staggered_world():
    """GW9 with one club playing on Monday (the rest on Saturday), and a midweek cup tie for
    one of that club's players on the Wednesday before."""
    w = build_world(n_teams=6, seasons=(2023, 2024), seed=9)
    db = w.db
    ms = db.query("SELECT * FROM matches WHERE season = ? AND matchday = ? ORDER BY kickoff",
                  [SEASON, GW])  # fmt: skip
    sat = ms["kickoff"].min()
    for i, m in ms.iterrows():  # every fixture on Saturday ...
        ms.at[i, "kickoff"] = sat
    mon = sat + timedelta(days=2)  # ... but the last one on Monday
    last = int(ms.iloc[-1]["match_id"])
    ms.loc[ms.match_id == last, "kickoff"] = mon
    ms["match_date"] = ms["kickoff"].dt.date
    db.upsert("matches", ms)
    for mid, ko in zip(ms["match_id"], ms["kickoff"], strict=True):
        db.con.execute("UPDATE fpl_points_gw SET kickoff = ? WHERE season = ? AND gw = ? AND "
                       "fixture_id = ?", [ko, SEASON, GW, int(mid)])  # fmt: skip
    club = int(ms.loc[ms.match_id == last, "home_team_id"].iloc[0])
    snap = db.query("SELECT player_id FROM fpl_players_gw WHERE season = ? AND gw = 8 AND "
                    "team_id = ? AND player_id IS NOT NULL", [SEASON, club])  # fmt: skip
    player = int(snap["player_id"].iloc[0])
    wed = mon - timedelta(days=5)  # public long before Saturday's deadline
    cup = ms.iloc[[-1]].assign(match_id=990077, competition_id="CUP-TEST", kickoff=wed,
                               match_date=wed.date(), matchday=None, finished=True)  # fmt: skip
    db.upsert("matches", cup)
    db.append("appearances", pd.DataFrame([{"player_id": player, "match_id": 990077,
                                            "team_id": club, "source": "test", "minutes": 90}]))  # fmt: skip
    return w, club, player, sat, mon, wed


def test_workload_uses_each_clubs_own_fixture_time_at_runtime_and_in_evaluation(staggered_world):
    w, club, player, sat, mon, wed = staggered_world
    db = w.db
    cutoff = pit.deadline(db, SEASON, GW).at
    ov = overlays.Workload(db)
    project.build_projection(db, SEASON, [GW, GW + 1], cutoff=cutoff, overlays=[ov],
                             cfg=SimConfig(n=100, seed=1), pool_gw=8)  # fmt: skip
    rt = ov.features.set_index("player_id")
    assert rt.loc[player, "kickoff"] == mon
    assert rt.loc[player, "xc_rest_days"] == pytest.approx(5.0)  # Wednesday to Monday
    assert rt.loc[player, "xc_mins_7d"] == 90
    ev = learning.walk_forward_rows(db, SEASON, GW).drop_duplicates("player_id")
    ev = ev.set_index("player_id")
    common = rt.index.intersection(ev.index)
    assert player in common and len(common) > 20
    for c in workload.FEATURES:
        np.testing.assert_allclose(rt.loc[common, c].to_numpy(float),
                                   ev.loc[common, c].to_numpy(float), equal_nan=True)  # fmt: skip
