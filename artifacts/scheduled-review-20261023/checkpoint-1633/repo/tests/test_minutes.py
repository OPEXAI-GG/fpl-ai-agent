"""Minutes model: no look-ahead, calibrated, no worse than the last-5 baseline, correct handling
of availability flags, horizon decay and brand-new players."""

import numpy as np
import pandas as pd
import pytest

from fpl_agent.model import features, minutes
from tests.synth import build_world


@pytest.fixture(scope="module")
def world():
    return build_world(n_teams=10, seasons=(2022, 2023, 2024), seed=11)


@pytest.fixture(scope="module")
def frame(world):
    return features.player_gw_frame(world.db)


def test_frame_has_one_row_per_player_fixture(world, frame):
    assert not frame.duplicated(["player_id", "season", "gw", "fixture_id"]).any()
    assert set(frame.columns) >= {"minutes", "started", "points", "position", "price", "team_id"}
    assert frame["started"].dtype == bool
    assert len(frame) == len(world.players) * 18 * 3


def test_features_never_see_the_row_they_predict(frame):
    base = features.minutes_features(frame)
    tampered = frame.copy()
    pid = frame.player_id.iloc[0]
    rows = tampered.index[tampered.player_id == pid]
    cut = rows[20]
    tampered.loc[cut:, "minutes"] = 90
    tampered.loc[cut:, "started"] = True
    new = features.minutes_features(tampered)
    cols = ["m3", "m5", "m10", "s3", "s5", "s10", "last_minutes", "zero_streak"]
    # rows up to and including the first changed one are unaffected: their windows end before it
    same = base.loc[rows[:21], cols].fillna(-1).equals(new.loc[rows[:21], cols].fillna(-1))
    assert same


def test_zero_streak_counts_consecutive_missed_fixtures():
    df = pd.DataFrame({"player_id": [1] * 6, "season": 1, "gw": range(1, 7),
                       "kickoff": pd.date_range("2024-08-01", periods=6, freq="7D"),
                       "minutes": [90, 0, 0, 0, 90, 0], "started": [True, False, False, False, True, False],
                       "position": "MID", "price": 60, "team_id": 1})  # fmt: skip
    out = features.minutes_features(df)
    assert out["zero_streak"].tolist() == [0, 0, 1, 2, 3, 0]


def test_model_is_calibrated_and_not_worse_than_baseline(frame):
    res = minutes.evaluate(frame, holdout_season=2024)
    assert res["n"] > 1000
    assert res["logloss_model"] <= res["logloss_baseline"] * 1.02
    assert abs(res["mean_pred"] - res["mean_actual"]) < 0.03


def test_probabilities_track_the_true_start_rate(world, frame):
    model = minutes.MinutesModel.fit(frame[frame.season < 2024])
    last = frame[frame.season < 2024]
    up = (
        last.groupby("player_id")
        .tail(1)[["player_id", "position", "team_id", "price"]]
        .assign(
            season=2024,
            gw=1,
            horizon=1,
            status="a",
            chance=100.0,
            kickoff=pd.Timestamp("2024-08-10"),
        )  # fmt: skip
    )
    out = model.forecast(last, up)
    truth = out.player_id.map(lambda p: world.players[p]["start_p"])
    assert np.corrcoef(out.p_start, truth)[0, 1] > 0.85
    assert (out.p_start.between(0, 1) & out.p_long.between(0, 1) & out.p_cameo.between(0, 1)).all()


def test_horizon_decays_toward_the_long_run_rate(frame):
    train = frame[frame.season < 2024]
    model = minutes.MinutesModel.fit(train)
    base = train.groupby("player_id").tail(1)[["player_id", "position", "team_id", "price"]]
    rows = pd.concat([base.assign(season=2024, gw=g, horizon=g, status="a", chance=100.0,
                                  kickoff=pd.Timestamp("2024-08-10") + pd.Timedelta(days=7 * g))
                      for g in (1, 2, 6)])  # fmt: skip
    out = model.forecast(train, rows)
    spread = out.groupby("horizon")["p_start"].std()
    assert spread[1] >= spread[2] >= spread[6]


@pytest.mark.parametrize(
    "status,chance,horizon,expected",
    [("a", None, 1, 1.0), ("a", 100, 3, 1.0), ("d", 50, 1, 0.5), ("i", 0, 1, 0.0),
     ("i", None, 1, 0.0), ("n", 0, 5, 0.0), ("u", 100, 1, 0.0), ("s", 0, 1, 0.0)],
)  # fmt: skip
def test_availability_factor(status, chance, horizon, expected):
    assert minutes.availability_factor(status, chance, horizon) == pytest.approx(expected)


def test_injured_players_recover_over_the_horizon():
    f = [minutes.availability_factor("i", 0, h) for h in range(1, 9)]
    assert f == sorted(f) and f[0] == 0.0 and 0.3 < f[-1] < 0.8
    d = [minutes.availability_factor("d", 25, h) for h in range(1, 5)]
    assert d[0] == pytest.approx(0.25) and d == sorted(d)
    assert minutes.availability_factor("s", 0, 3) > minutes.availability_factor("i", 0, 3)


def test_flagged_out_player_gets_near_zero_start_probability(frame):
    train = frame[frame.season < 2024]
    model = minutes.MinutesModel.fit(train)
    star = train.groupby("player_id")["started"].mean().idxmax()
    row = train[train.player_id == star].tail(1)[["player_id", "position", "team_id", "price"]]
    ok = model.forecast(train, row.assign(season=2024, gw=1, horizon=1, status="a", chance=100.0,
                                          kickoff=pd.Timestamp("2024-08-10")))  # fmt: skip
    out = model.forecast(train, row.assign(season=2024, gw=1, horizon=1, status="i", chance=0.0,
                                           kickoff=pd.Timestamp("2024-08-10")))  # fmt: skip
    assert ok.p_start.iloc[0] > 0.6 and out.p_start.iloc[0] < 0.01


def test_new_player_without_history_falls_back_to_position_average(frame):
    train = frame[frame.season < 2024]
    model = minutes.MinutesModel.fit(train)
    new = pd.DataFrame({"player_id": [10**6], "position": ["MID"], "team_id": [1], "price": [55],
                        "season": [2024], "gw": [1], "horizon": [1], "status": ["a"],
                        "chance": [100.0], "kickoff": [pd.Timestamp("2024-08-10")]})  # fmt: skip
    out = model.forecast(train, new)
    assert 0 < out.p_start.iloc[0] < 0.9


def test_calibration_table_bins():
    rng = np.random.default_rng(0)
    p = rng.uniform(0, 1, 5000)
    y = rng.random(5000) < p
    table = minutes.calibration_table(p, y, bins=5)
    assert (abs(table.pred - table.actual) < 0.05).all() and table.n.sum() == 5000


def test_a_missing_chance_of_playing_may_be_pandas_na():
    availability_factor = minutes.availability_factor
    assert availability_factor("a", pd.NA, 1) == 1.0
    assert availability_factor("d", pd.NA, 1) == 1.0
    assert availability_factor("i", pd.NA, 2) < 1.0


def test_history_without_its_own_snapshot_keeps_price_status_team_unknown(world):
    """F-17: a gameweek with no snapshot of its own gets only the season's agreed position;
    price, status, team and chance stay unknown, and a later snapshot never fills them."""
    db = world.db
    season = 2024
    full = features.player_gw_frame(db)
    keep = db.query(f"SELECT * FROM fpl_players_gw WHERE season = {season}")
    try:
        db.con.execute(f"DELETE FROM fpl_players_gw WHERE season = {season} AND gw <= 5")
        f = features.player_gw_frame(db)
        early = f[(f["season"] == season) & (f["gw"] <= 5)]
        assert len(early) and early["position_source"].eq("season").all()
        assert early[["price", "status", "team_id", "chance"]].isna().all().all()
        ref = full.set_index(["player_id", "season", "gw", "fixture_id"])["position"]
        got = early.set_index(["player_id", "season", "gw", "fixture_id"])["position"]
        assert got.equals(ref.loc[got.index])  # the season's position is the one it had
        later = f[(f["season"] == season) & (f["gw"] > 5)]
        assert later["position_source"].eq("snapshot").all()
        # a season whose snapshots disagree on a player's position gives him none
        pid = int(early["player_id"].iloc[0])
        db.con.execute(
            f"UPDATE fpl_players_gw SET position = 'GKP' WHERE season = {season} AND gw = 6 "
            f"AND player_id = {pid} AND position <> 'GKP'"
        )
        if db.con.execute(f"SELECT count(DISTINCT position) FROM fpl_players_gw WHERE "
                          f"season = {season} AND player_id = {pid}").fetchone()[0] > 1:  # fmt: skip
            g = features.player_gw_frame(db)
            mine = g[(g["season"] == season) & (g["gw"] <= 5) & (g["player_id"] == pid)]
            assert mine["position"].isna().all() and mine["position_source"].isna().all()
    finally:
        db.con.execute(f"DELETE FROM fpl_players_gw WHERE season = {season}")
        db.con.register("_keep", keep)
        db.con.execute("INSERT INTO fpl_players_gw SELECT * FROM _keep")
        db.con.unregister("_keep")


def test_season_position_fill_is_cutoff_invariant(world):
    """A later snapshot (here: one that disagrees on a player's position) cannot change, add or
    remove the season fill an earlier cutoff sees; before any snapshot there is no fill."""
    db = world.db
    season = 2024
    keep = db.query(f"SELECT * FROM fpl_players_gw WHERE season = {season}")
    try:
        db.con.execute(f"DELETE FROM fpl_players_gw WHERE season = {season} AND gw <= 5")
        first, last = db.con.execute(
            f"SELECT min(snapshot_at), max(snapshot_at) FROM fpl_players_gw WHERE season = {season}"
        ).fetchone()
        frame = features.player_gw_frame(db)
        early = frame[(frame["season"] == season) & (frame["gw"] <= 5)]
        at_cut = features.positions_as_of(early, db, last)
        assert at_cut["position"].equals(early["position"])  # same evidence, same answer
        none_yet = features.positions_as_of(early, db, first - pd.Timedelta(seconds=1))
        assert none_yet["position"].isna().all() and none_yet["position_source"].isna().all()
        pid = int(early["player_id"].iloc[0])
        later = last + pd.Timedelta(days=30)
        db.con.execute(
            f"INSERT INTO fpl_players_gw (season, gw, snapshot_at, fpl_id, player_id, position) "
            f"SELECT season, 38, ?, fpl_id, player_id, CASE WHEN position = 'GKP' THEN 'FWD' "
            f"ELSE 'GKP' END FROM fpl_players_gw WHERE season = {season} AND player_id = {pid} "
            "LIMIT 1",
            [later],
        )
        whole = features.player_gw_frame(db)  # the whole database now disagrees for pid
        mine = whole[(whole["season"] == season) & (whole["gw"] <= 5) & (whole["player_id"] == pid)]
        assert mine["position"].isna().all()
        again = features.positions_as_of(early, db, last)
        assert again["position"].equals(at_cut["position"])  # the earlier cutoff is unchanged
    finally:
        db.con.execute(f"DELETE FROM fpl_players_gw WHERE season = {season}")
        db.con.register("_keep", keep)
        db.con.execute("INSERT INTO fpl_players_gw SELECT * FROM _keep")
        db.con.unregister("_keep")
