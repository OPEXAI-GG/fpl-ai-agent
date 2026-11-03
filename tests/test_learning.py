"""Weekly learning loop: issued forecasts scored on identical cohorts, thresholds fixed before
evaluation, candidate vs current, insufficient evidence retains the current state, rollback,
repeatability, and the historical walk-forward keeps tuning apart from the final test."""

import json
from datetime import datetime, timedelta

import numpy as np
import pandas as pd
import pytest

from fpl_agent import learning, pit, review
from fpl_agent.db.database import FootballDB
from fpl_agent.model import overlays

N = 40  # players
AFFECTED = range(0, 20)  # players the news feature changes


def deadline(gw):
    return datetime(2026, 8, 21, 17, 30) + timedelta(weeks=gw - 1)


def week(db, gw, enriched_better=True, final=True, checked=True, seed=0):
    rng = np.random.default_rng(seed + gw)
    db.upsert("fpl_events", pd.DataFrame([{"season": 2026, "gw": gw, "deadline_time": deadline(gw),
                                           "finished": final, "data_checked": checked,
                                           "observed_at": deadline(gw)}]))  # fmt: skip
    actual = rng.integers(0, 10, N)
    db.append("fpl_points_gw", pd.DataFrame({
        "season": 2026, "gw": gw, "fpl_id": range(N), "player_id": range(N), "fixture_id": gw,
        "minutes": 90, "total_points": actual,
        "stats": [json.dumps({"starts": 1}) for _ in range(N)], "explain": "[]"}))  # fmt: skip
    base = actual + rng.normal(0, 2.0, N)
    better = actual + rng.normal(0, 0.5, N)  # the evidence was right
    worse = actual + rng.normal(0, 4.0, N)
    enr = base.copy()
    for i in AFFECTED:
        enr[i] = better[i] if enriched_better else worse[i]
    made = deadline(gw) - timedelta(hours=5)
    for run, xp, arm, variant, parent in (
        (f"b{gw}", base, "baseline", "primary", None),
        (f"e{gw}", enr, "enriched", "shadow", f"b{gw}"),
    ):
        det = [json.dumps({"p_start": 0.8, "p_cameo": 0.1,
                           "evidence": [{"state": "applied"}] if arm == "enriched" and i in AFFECTED
                           else []}) for i in range(N)]  # fmt: skip
        db.append("projections", pd.DataFrame({
            "run_id": run, "model": "sim-v1", "created_at": made, "season": 2026, "gw": gw,
            "player_id": range(N), "xpts": xp, "detail": det}))  # fmt: skip
        pit.register_run(db, run, 2026, [gw], made, made, variant=variant, parent_run_id=parent,
                         flags={"arm": arm})  # fmt: skip


@pytest.fixture
def db():
    d = FootballDB()
    d.append("players", pd.DataFrame({"player_id": range(N), "name": [f"P{i}" for i in range(N)],
                                      "position": ["MID"] * N}))  # fmt: skip
    return d


def test_evaluation_scores_both_arms_on_the_identical_cohort(db):
    week(db, 1)
    db.con.execute("DELETE FROM projections WHERE run_id = 'e1' AND player_id = 39")
    ev = learning.evaluate_gw(db, 2026, 1)
    assert ev["cohort"] == N - 1
    assert ev["arms"]["baseline"]["n"] == ev["arms"]["enriched"]["n"] == N - 1
    assert ev["arms"]["enriched"]["evidence_coverage"] == pytest.approx(20 / 39, abs=1e-3)
    assert {"mae", "rmse", "bias", "start_brier", "start_logloss", "play_brier",
            "start_calibration"} <= set(ev["arms"]["baseline"])  # fmt: skip
    assert db.con.execute("SELECT count(*) FROM evaluations WHERE gw = 1").fetchone()[0] == 2


def test_post_deadline_runs_are_never_the_issued_forecast(db):
    week(db, 1)
    late = deadline(1) + timedelta(days=1)
    db.append("projections", pd.DataFrame({"run_id": "late", "created_at": late, "season": 2026,
                                           "gw": 1, "player_id": range(N), "xpts": 0.0}))  # fmt: skip
    pit.register_run(db, "late", 2026, [1], late, late, flags={"arm": "baseline"})
    assert learning.evaluate_gw(db, 2026, 1)["primary_run"] == "b1"


def test_provisional_week_changes_nothing(db):
    week(db, 1, checked=False)
    res = learning.weekly_update(db, 2026, 1)
    assert res.status == "not_final" and learning.current_state(db) is None
    assert db.con.execute("SELECT count(*) FROM evaluations").fetchone()[0] == 0


def test_insufficient_evidence_retains_the_current_state(db):
    for gw in (1, 2):
        week(db, gw)
        res = learning.weekly_update(db, 2026, gw)
    assert res.status == "retained" and "insufficient evidence" in res.reason
    assert learning.current_state(db) is None


def test_enough_paired_evidence_promotes_and_regression_demotes(db, monkeypatch):
    monkeypatch.delenv("FEATURE_NEWS_AVAILABILITY", raising=False)
    for gw in (1, 2, 3, 4):
        week(db, gw, enriched_better=True)
        res = learning.weekly_update(db, 2026, gw)
    assert res.status == "promoted"
    st = learning.current_state(db)
    assert st["config"]["features"]["news_availability"] == "on"
    assert st["config"]["features"]["workload"] == "shadow"  # it changed nobody: no evidence
    assert overlays.modes(st["config"]["features"])["news_availability"] == "on"
    ev = json.loads(db.query("SELECT evaluation FROM learning_state WHERE status = 'current'")
                    ["evaluation"].iloc[0])  # fmt: skip
    assert ev["thresholds"] == learning.THRESHOLDS  # fixed before the evaluation, reported
    # the same week again proposes nothing new (repeatable)
    again = learning.weekly_update(db, 2026, 4)
    assert again.status == "retained" and learning.current_state(db)["version"] == st["version"]
    for gw in range(5, 15):
        week(db, gw, enriched_better=False)
        learning.weekly_update(db, 2026, gw)
    assert learning.current_state(db)["config"]["features"]["news_availability"] == "shadow"


def test_rollback_restores_the_previous_version(db):
    for gw in (1, 2, 3, 4):
        week(db, gw)
        learning.weekly_update(db, 2026, gw)
    v = learning.current_state(db)["version"]
    res = learning.rollback(db)
    assert res.status == "rolled_back" and learning.current_state(db) is None  # defaults again
    learning.rollback(db, to_version=v)
    cur = learning.current_state(db)
    assert cur["config"]["features"]["news_availability"] == "on" and cur["parent_version"] == v
    statuses = learning.history(db)["status"].tolist()
    assert statuses.count("current") == 1 and "rolled_back" in statuses
    assert learning.rollback(db, to_version=999).reason == "no version 999"


def _review(db, gw, pred, act):
    db.append("reviews", pd.DataFrame([{
        "season": 2026, "gw": gw, "created_at": datetime(2026, 9, 1), "kind": "prediction",
        "payload": json.dumps({"final": True, "eligible": True, "by_position": {
            "MID": {"n": 4000, "mean_pred": pred, "mean_actual": act}}})}]))  # fmt: skip


def test_calibration_step_is_bounded_and_validated_on_a_holdout_week(db):
    for gw in (1, 2, 3):
        week(db, gw)
        _review(db, gw, 3.0, 4.5)  # projections far too low in every review
    res = learning.weekly_update(db, 2026, 3)
    st = learning.current_state(db)
    assert res.evaluation["holdout_mae_candidate"] is not None
    if res.status == "promoted":
        assert st["config"]["calibration"]["MID"] == pytest.approx(1 + learning.MAX_CAL_STEP)
    else:  # the holdout week said the bigger factor would hurt: nothing adopted
        assert res.status == "rejected" and st is None


def test_candidate_worse_on_the_holdout_is_rejected(db, monkeypatch):
    for gw in (1, 2):
        week(db, gw)
        _review(db, gw, 3.0, 4.5)
    monkeypatch.setitem(learning.THRESHOLDS, "max_regression", -10.0)  # demand a huge gain
    res = learning.weekly_update(db, 2026, 2)
    assert res.status == "rejected" and learning.current_state(db) is None
    assert learning.history(db)["status"].tolist() == ["rejected"]


def test_review_after_the_fix_feeds_the_learning_loop(db):
    week(db, 1)
    rep = review.prediction_review(db, 2026, 1)
    assert rep["final"] and rep["run"]["run_id"] == "b1"


# ---- historical walk-forward ------------------------------------------------------------------
def test_walk_forward_keeps_tuning_and_test_apart():
    from tests.synth import build_world

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
