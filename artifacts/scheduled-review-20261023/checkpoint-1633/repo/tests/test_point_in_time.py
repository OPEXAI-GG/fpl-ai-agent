"""No look-ahead: a projection made at a decision cutoff cannot change when data from after the
cutoff is added (odds, closing prices, player snapshots, late fixtures, translation fits); and
reviews score one pre-deadline forecast against every fixture with FPL's real scoring rules."""

import json
from datetime import datetime, timedelta

import pandas as pd
import pytest

from fpl_agent import review
from fpl_agent.db.database import FootballDB
from fpl_agent.model import project, translation
from fpl_agent.model.simulate import SimConfig
from tests.synth import build_world

GW = 10


@pytest.fixture(scope="module")
def world():
    return build_world(n_teams=6, seasons=(2023, 2024), seed=11)


def first_kickoff(db, season, gw):
    return db.con.execute(
        "SELECT min(kickoff) FROM matches WHERE season = ? AND matchday = ?", [season, gw]
    ).fetchone()[0]


def projected(db, cutoff):
    p = project.build_projection(db, 2024, [GW], cfg=SimConfig(n=300, seed=4), pool_gw=GW,
                                 cutoff=cutoff)  # fmt: skip
    return p.frame().set_index("player_id").sort_index()["xpts"]


def odds_row(mid, at, closing, home=1.3):
    return {"match_id": mid, "source": "test", "bookmaker": "b", "closing": closing,
            "home": home, "draw": 6.0, "away": 11.0, "over25": 1.5, "under25": 2.6,
            "ah_line": None, "ah_home": None, "ah_away": None, "ingested_at": at}  # fmt: skip


def test_data_from_after_the_cutoff_cannot_change_a_projection(world):
    db = world.db
    cutoff = project.approx_deadline(first_kickoff(db, 2024, GW))
    before = projected(db, cutoff)
    mids = [
        r[0]
        for r in db.con.execute(
            "SELECT match_id FROM matches WHERE season = 2024 AND matchday = ?", [GW]
        ).fetchall()
    ]
    late = cutoff + timedelta(hours=1)
    # closing and post-cutoff prices, a later player snapshot (everyone injured), a fixture of
    # the previous gameweek played after the cutoff, and a translation fit made later
    rows = [odds_row(m, late, closing) for m in mids for closing in (True, False)]
    db.upsert("match_odds", pd.DataFrame(rows))
    db.log_odds(pd.DataFrame(rows))
    snap = db.query("SELECT * FROM fpl_players_gw WHERE season = 2024 AND gw = ?", [GW])
    db.upsert("fpl_players_gw", snap.assign(snapshot_at=late, status="i", chance_next=0))
    pts = db.query("SELECT * FROM fpl_points_gw WHERE season = 2024 AND gw = ? LIMIT 40",
                   [GW - 1])  # fmt: skip
    db.upsert("fpl_points_gw", pts.assign(fixture_id=pts["fixture_id"] + 100000, minutes=0,
                                          total_points=-5, kickoff=late))  # fmt: skip
    translation.store(db, pd.DataFrame([{"from_comp": "ENG2", "to_comp": "ENG1",
                                         "stat": "xg_share", "factor": 3.0, "n_players": 9,
                                         "minutes": 9000.0}]))  # fmt: skip
    db.con.execute("UPDATE league_translation SET updated_at = ?", [late])
    after = projected(db, cutoff)
    pd.testing.assert_series_equal(before, after)


def test_prices_known_before_the_cutoff_are_used(world):
    db = FootballDB()
    mid = 1
    cut = datetime(2024, 10, 12, 13, 30)
    db.append("match_odds_log", pd.DataFrame([odds_row(mid, cut - timedelta(hours=5), False)]))
    db.append("match_odds_log", pd.DataFrame([odds_row(mid, cut + timedelta(hours=1), False,
                                                       home=4.0)]))  # fmt: skip
    db.upsert("match_odds", pd.DataFrame([odds_row(mid, cut + timedelta(days=2), True, 9.0)]))
    early = project.market_lambdas(db, mid, cut)
    assert early is not None and early[0] > early[1]  # the 1.30 favourite, not the later prices
    assert project.market_lambdas(db, mid) != early  # without a cutoff: the newest/closing price
    empty = FootballDB()
    empty.upsert("match_odds", pd.DataFrame([odds_row(mid, cut + timedelta(days=2), True)]))
    assert project.market_lambdas(empty, mid, cut) is None  # closing prices are never used
    empty.upsert("match_odds", pd.DataFrame([odds_row(mid, cut + timedelta(days=2), False)]))
    assert project.market_lambdas(empty, mid, cut) is not None  # reconstruction: labelled
    assert project.market_lambdas(empty, mid, cut, known_at=True) is None  # strict known-at


def test_translation_fits_are_loaded_as_of_the_cutoff():
    db = FootballDB()
    translation.store(db, pd.DataFrame([{"from_comp": "ENG2", "to_comp": "ENG1", "stat": "xg_share",
                                         "factor": 0.8, "n_players": 5, "minutes": 5000.0}]))  # fmt: skip
    stamp = db.con.execute("SELECT updated_at FROM league_translation").fetchone()[0]
    assert translation.Translations.load(db).factor("ENG2", "xg_share") == 0.8
    old = translation.Translations.load(db, as_of=stamp - timedelta(days=1))
    assert old.factor("ENG2", "xg_share") == translation.prior("ENG2")


def test_backtest_and_replay_cut_before_the_first_kickoff():
    ko = datetime(2025, 8, 16, 11, 30)
    assert project.approx_deadline(ko) == datetime(2025, 8, 16, 10, 0)
    import inspect

    from fpl_agent.model import backtest, replay

    for mod in (backtest.projection_backtest, replay.replay_season):
        src = inspect.getsource(mod)
        assert "cutoff=cutoff" in src and "approx_deadline" in src


# ---- reviews -----------------------------------------------------------------------------------
POS = {1: "GKP", 2: "GKP", **{i: "DEF" for i in range(3, 8)},
       **{i: "MID" for i in range(8, 13)}, **{i: "FWD" for i in range(13, 16)}}  # fmt: skip
XI = [1, 3, 4, 5, 6, 8, 9, 10, 11, 13, 14]
BENCH = [12, 7, 15, 2]


def review_db(points, minutes, chip=None, captain=8, vice=9, dgw=None):
    db = FootballDB()
    db.upsert("fpl_players_gw", pd.DataFrame([{
        "season": 2026, "gw": 5, "snapshot_at": datetime(2026, 9, 1), "fpl_id": p, "player_id": p,
        "position": pos} for p, pos in POS.items()]))  # fmt: skip
    rows = []
    for p in POS:
        fixtures = (
            dgw.get(p, [(points.get(p, 0), minutes.get(p, 90), "[]")])
            if dgw
            else [(points.get(p, 0), minutes.get(p, 90), "[]")]
        )
        for fx, (pt, mi, ex) in enumerate(fixtures, start=1):
            rows.append({"season": 2026, "gw": 5, "fpl_id": p, "player_id": p, "fixture_id": fx,
                         "minutes": mi, "total_points": pt, "explain": ex})  # fmt: skip
    db.upsert("fpl_points_gw", pd.DataFrame(rows))
    payload = {"action": "x", "chip": chip, "transfers_in": [], "transfers_out": [], "hits": 0,
               "captain": captain, "vice": vice, "xi": XI, "bench": BENCH, "xp": 50.0}  # fmt: skip
    db.append("decisions", pd.DataFrame([{"season": 2026, "gw": 5, "created_at": datetime(2026, 9, 1),
                                          "mode": "dry", "payload": json.dumps(payload)}]))  # fmt: skip
    return db


def test_a_no_show_captain_hands_the_armband_to_the_vice_and_is_auto_subbed():
    pts = {p: 2 for p in POS} | {8: 0, 9: 10, 12: 6}
    rep = review.decision_review(review_db(pts, {8: 0}), 2026, 5)
    # captain 8 played 0: 12 (first on the bench, a MID) comes on; vice 9 is doubled
    assert rep["captain_points"] == 10
    assert rep["xi_points"] == sum(pts[p] for p in XI if p != 8) + 6 + 10


def test_bench_boost_and_triple_captain_are_scored():
    pts = {p: 3 for p in POS}
    bb = review.decision_review(review_db(pts, {}, chip="bboost"), 2026, 5)
    assert bb["xi_points"] == 15 * 3 + 3 and bb["bench_points_left"] == 0
    tc = review.decision_review(review_db(pts, {}, chip="3xc"), 2026, 5)
    assert tc["xi_points"] == 11 * 3 + 2 * 3


def test_double_gameweek_components_use_every_fixture():
    ex1 = json.dumps([{"identifier": "goals_scored", "points": 5, "value": 1}])
    ex2 = json.dumps([{"identifier": "goals_scored", "points": 5, "value": 1},
                      {"identifier": "assists", "points": 3, "value": 1}])  # fmt: skip
    db = review_db({}, {}, dgw={8: [(7, 90, ex1), (10, 90, ex2)]})
    act = review._actuals(db, 2026, 5).set_index("player_id")
    assert act.loc[8, "actual"] == 17 and act.loc[8, "minutes"] == 180
    comps = review._explain_components(act.loc[8, "explain"])
    assert comps == {"goals_scored": 10.0, "assists": 3.0}


def test_prediction_review_uses_one_run_from_before_the_deadline():
    db = review_db({p: 2 for p in POS}, {})
    db.match_id("fpl", 77, "ENG1", 2026, datetime(2026, 9, 6).date(), *_teams(db), matchday=5,
                kickoff=datetime(2026, 9, 6, 11, 30), finished=True)  # fmt: skip
    for run, at, xp in (
        ("early", datetime(2026, 9, 6, 8), 2.0),
        ("late", datetime(2026, 9, 6, 10, 30), 9.0),
    ):  # after the 10:00 cutoff
        db.append("projections", pd.DataFrame({
            "run_id": run, "model": "m", "created_at": at, "season": 2026, "gw": 5,
            "player_id": list(POS), "xpts": xp, "detail": "{}"}))  # fmt: skip
    rep = review.prediction_review(db, 2026, 5)
    assert rep["projection_run"] == "early" and rep["bias"] == 0.0
    assert set(rep["by_position"]) == {"GKP", "DEF", "MID", "FWD"}  # this season's positions


def _teams(db):
    return db.team_id("fpl", 1, "Arsenal"), db.team_id("fpl", 2, "Chelsea")


def test_official_deadlines_are_versioned_observations():
    """A deadline is stored once per value; a moved deadline is a new version, and an as-of
    reader sees the version known at that time (none before the first observation)."""
    from copy import deepcopy
    from datetime import datetime

    from fpl_agent.db import ingest_fpl
    from fpl_agent.db.database import FootballDB
    from fpl_agent.db.gameweeks import official_deadline
    from fpl_agent.review import _cutoff
    from tests.helpers import fixture

    db = FootballDB()
    boot = fixture("bootstrap-static.json")
    t1, t2, t3 = datetime(2026, 8, 1), datetime(2026, 8, 2), datetime(2026, 8, 3)
    ingest_fpl.load_bootstrap(db, boot, 2026, 1, snapshot_at=t1)
    ingest_fpl.load_bootstrap(db, boot, 2026, 1, snapshot_at=t2)
    events = len([e for e in boot["events"] if e.get("deadline_time")])
    assert db.con.execute("SELECT count(*) FROM fpl_deadlines").fetchone()[0] == events
    moved = deepcopy(boot)
    moved["events"][0]["deadline_time"] = "2026-08-22T10:00:00Z"
    ingest_fpl.load_bootstrap(db, moved, 2026, 1, snapshot_at=t3)
    assert db.con.execute("SELECT count(*) FROM fpl_deadlines").fetchone()[0] == events + 1
    gw1 = boot["events"][0]["id"]
    first = datetime.fromisoformat(boot["events"][0]["deadline_time"].replace("Z", ""))
    assert official_deadline(db, 2026, gw1, as_of=datetime(2026, 7, 1)) is None
    assert official_deadline(db, 2026, gw1, as_of=t2) == first
    assert official_deadline(db, 2026, gw1, as_of=t3) == datetime(2026, 8, 22, 10)
    assert _cutoff(db, 2026, gw1) == datetime(2026, 8, 22, 10)  # reviews use the official one
