"""Weekly learning loop, exercised through the paths the agent really uses: `agent.run_weekly`
issues the forecast (and its experiment arms) for a gameweek, reviews come from
`review.prediction_review`, and only then does `learning.evaluate_gw` /
`learning.weekly_update` score and decide.

The feature whose lifecycle is tested is a stand-in registered for these tests only
(`ruled_out`: it marks chosen players as out of the next gameweek), next to the real workload
overlay. It goes through the same registry, flags, arms, evaluation and decisions as a real
feature, and the test chooses whether its call was right, which no real feature allows.

What is pinned here:

* the same evidence processed twice changes nothing (no compounding calibration), a corrected
  outcome is re-applied from the state it was first applied to, and a repeated update never
  undoes a rollback;
* every enabled feature gets its own matched comparison (same models, samples and seeds) in
  every supported off/shadow/on combination, with a null-intervention arm that reproduces the
  issued forecast exactly, so a feature keeps gathering evidence after it is promoted;
* arms that were made after the deadline, by another kind of run or with other artifact
  versions never count, and the issued forecast scored is the one the planner received.
"""

import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

from fpl_agent import agent, learning, lessons, pit, review
from fpl_agent.agent import RunOptions, run_weekly
from fpl_agent.config import Settings
from fpl_agent.model import calibration, overlays, project
from fpl_agent.model.minutes import EPS
from tests.synth import build_world

SEASON = 2024
FEAT = "ruled_out"
RESET = ("learning_state", "learning_updates", "forecast_runs", "projections", "evaluations",
         "reviews", "decisions", "lessons", "fpl_events")  # fmt: skip
TARGETS: dict[int, set[int]] = {}  # gw -> players the stand-in feature rules out


class RuledOut:
    """Stand-in feature: the chosen players are out of the next gameweek (availability 0)."""

    name = FEAT

    def __init__(self, db, cache=None):
        self.changed: list[int] = []

    def apply(self, mins, upcoming, cutoff, ctx=None):
        out = mins.copy()
        out[FEAT] = [{} for _ in range(len(out))]
        for i, r in out.iterrows():
            if r.horizon == 1 and int(r.player_id) in TARGETS.get(int(r.gw), set()):
                out.at[i, FEAT] = {"from": round(float(r.availability), 3), "to": 0.0}
                out.at[i, "availability"] = 0.0
                out.at[i, "p_start"] = EPS
                out.at[i, "p_cameo"] = EPS
                self.changed.append(int(r.player_id))
        return out

    def summary(self):
        return {"changed_next_gw": len(self.changed)}


class FakeFPL:
    def __init__(self, pool, squad_ids, gw, deadline):
        self.pool, self.squad, self.gw, self.deadline = pool, squad_ids, gw, deadline

    def bootstrap(self):
        ev = SimpleNamespace(id=self.gw, is_next=True, deadline_time=self.deadline)
        return SimpleNamespace(events=[ev])

    def bootstrap_raw(self):
        codes = {"GKP": 1, "DEF": 2, "MID": 3, "FWD": 4}
        return {"elements": [{"id": int(r.fpl_id), "element_type": codes[r.position],
                              "team": int(r.team_id), "now_cost": int(r.price),
                              "cost_change_start": 0} for r in self.pool.itertuples()]}  # fmt: skip

    def entry_picks_raw(self, entry, gw):
        return {"picks": [{"element": i, "position": n + 1} for n, i in enumerate(self.squad)],
                "entry_history": {"bank": 30, "value": 1000}}  # fmt: skip

    def entry_transfers_raw(self, entry):
        return []

    def entry_history_raw(self, entry):
        return {"chips": [], "current": []}


@pytest.fixture(scope="module")
def world():
    w = build_world(n_teams=6, seasons=(2023, 2024), seed=5)
    pool = project.current_pool(w.db, SEASON, 8)
    need, per_club, squad = {"GKP": 2, "DEF": 5, "MID": 5, "FWD": 3}, {}, []
    for r in pool.sort_values("price").itertuples():
        if need[r.position] and per_club.get(r.team_id, 0) < 3:
            squad.append(int(r.fpl_id))
            need[r.position] -= 1
            per_club[r.team_id] = per_club.get(r.team_id, 0) + 1
    w.squad, w.pool = squad, pool
    w.points = w.db.query("SELECT * FROM fpl_points_gw")
    return w


@pytest.fixture
def db(world, monkeypatch):
    for k in ("ANTHROPIC_API_KEY", "ODDS_API_KEY", "AGENT_MODE", "AGENT_ENABLED",
              "FEATURE_RULED_OUT", "FEATURE_WORKLOAD"):  # fmt: skip
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setattr(overlays, "REGISTRY", {**overlays.REGISTRY, FEAT: RuledOut})
    monkeypatch.setattr(overlays, "FEATURES", (FEAT, *overlays.FEATURES))
    TARGETS.clear()
    have = {r[0] for r in world.db.con.execute("SHOW TABLES").fetchall()}
    for t in RESET:
        if t in have:
            world.db.con.execute(f"DELETE FROM {t}")
    world.db.con.execute("DELETE FROM fpl_points_gw")
    world.db.append("fpl_points_gw", world.points)
    return world.db


def deadline(db, gw) -> datetime:
    return pit.deadline(db, SEASON, gw).at


def issue(world, gw, samples=150, now=None):
    """The agent's real weekly run for `gw`, two hours before its deadline."""
    at = (now or deadline(world.db, gw) - timedelta(hours=2)).replace(tzinfo=UTC)
    fpl = FakeFPL(world.pool, world.squad, gw, deadline(world.db, gw).replace(tzinfo=UTC))
    res = run_weekly(world.db, Settings(), RunOptions(season=SEASON, horizon=2, entry_id=1,
                                                      samples=samples, use_chips=False,
                                                      force=True),
                     fpl=fpl, now=at)  # fmt: skip
    assert res.status == "ok", res.notes
    return res


def finish(db, gw, checked=True):
    db.upsert("fpl_events", pd.DataFrame([{"season": SEASON, "gw": gw,
                                           "deadline_time": deadline(db, gw), "finished": True,
                                           "data_checked": checked,
                                           "observed_at": deadline(db, gw)}]))  # fmt: skip


def inflate(db, gw, extra):
    db.con.execute("UPDATE fpl_points_gw SET total_points = total_points + ? WHERE season = ? "
                   "AND gw = ?", [extra, SEASON, gw])  # fmt: skip


def runs(db, parent):
    df = db.query("SELECT * FROM forecast_runs WHERE parent_run_id = ?", [parent])
    df["flags"] = [json.loads(f) for f in df["flags"]]
    return df


def primary_run(db, gw):
    return pit.eligible_run(db, SEASON, gw).run_id


def rule_out(gw, players):
    """The stand-in feature will rule these players out of `gw`."""
    TARGETS[gw] = set(players)


def starters(world, gw, played: bool, n=12):
    """Regular starters (started the week before) who did not play in `gw`, or who played and
    scored well (so a wrong "ruled out" call makes the forecast worse)."""
    p = world.points
    prev = set(p[(p.season == SEASON) & (p.gw == gw - 1) & (p.minutes >= 60)].player_id)
    now = p[(p.season == SEASON) & (p.gw == gw)]
    sel = now[(now.minutes > 0) & (now.total_points >= 5)] if played else now[now.minutes == 0]
    return [int(x) for x in sel.player_id if int(x) in prev][:n]


# ---- 1. idempotent updates, corrected outcomes, effective rollback ----------------------------
def _underpredicted_weeks(world, gws=(7, 8, 9)):
    for gw in gws:
        issue(world, gw)
        inflate(world.db, gw, 15)  # every player scores far more than projected
        finish(world.db, gw)
        assert review.prediction_review(world.db, SEASON, gw)["final"]


def test_identical_evidence_processed_twice_changes_nothing(world, db):
    _underpredicted_weeks(world)
    factors = []
    for _ in range(4):
        learning.weekly_update(db, SEASON, 9)
        factors.append(learning.current_state(db)["config"]["calibration"]["MID"])
    assert factors == [1.03, 1.03, 1.03, 1.03]
    again = learning.weekly_update(db, SEASON, 9)
    assert again.status == "unchanged" and "already processed" in again.reason
    assert learning.history(db)["status"].tolist().count("current") == 1


def test_a_corrected_outcome_is_reapplied_from_the_state_before_it(world, db):
    _underpredicted_weeks(world)
    first = learning.weekly_update(db, SEASON, 9)
    assert first.status == "promoted"
    inflate(db, 9, 5)  # FPL corrects the gameweek's points
    review.prediction_review(db, SEASON, 9)
    res = learning.weekly_update(db, SEASON, 9)
    assert res.status in ("corrected", "unchanged_after_correction")
    st = learning.current_state(db)
    assert st["config"]["calibration"]["MID"] <= 1.0 + learning.MAX_CAL_STEP  # one step, not two
    assert st["parent_version"] is None  # re-derived from the state before GW9's update


def test_rollback_reaches_the_agent_and_a_repeated_update_does_not_undo_it(world, db):
    _underpredicted_weeks(world)
    learning.weekly_update(db, SEASON, 9)
    st = learning.current_state(db)
    issue(world, 10)
    flags = json.loads(db.con.execute("SELECT flags FROM forecast_runs WHERE run_id = ?",
                                      [primary_run(db, 10)]).fetchone()[0])  # fmt: skip
    assert flags["config"]["calibration"] == st["config"]["calibration"]
    assert flags["config"]["learning_version"] == st["version"]
    assert learning.rollback(db).status == "rolled_back"
    assert learning.current_state(db) is None
    for _ in range(2):  # the same evidence again: the rollback stays
        assert learning.weekly_update(db, SEASON, 9).status == "unchanged"
    assert learning.current_state(db) is None
    issue(world, 10, now=deadline(db, 10) - timedelta(hours=1))  # the next run after rollback
    flags = json.loads(db.con.execute("SELECT flags FROM forecast_runs WHERE run_id = ?",
                                      [primary_run(db, 10)]).fetchone()[0])  # fmt: skip
    assert flags["config"]["learning_version"] is None
    assert flags["config"]["calibration"] == calibration.factors(db, SEASON, 10)
    assert flags["modes"] == {FEAT: "shadow", "workload": "shadow"}


# ---- 2. matched experiments in every off/shadow/on combination --------------------------------
def _pairs(arms: pd.DataFrame, feature: str) -> list[tuple[str, str]]:
    out = []
    for a in arms.itertuples():
        for b in arms.itertuples():
            fa, fb = set(a.flags["features_on"]), set(b.flags["features_on"])
            if fa - fb == {feature} and fb <= fa and a.flags["samples"] == b.flags["samples"] \
                    and a.flags["seed"] == b.flags["seed"]:  # fmt: skip
                out.append((a.run_id, b.run_id))
    return out


@pytest.mark.parametrize("probe_mode, workload_mode", [
    ("shadow", "shadow"), ("on", "shadow"), ("on", "on"), ("off", "on"), ("shadow", "off"),
])  # fmt: skip
def test_every_enabled_feature_gets_a_matched_comparison(world, db, monkeypatch, probe_mode,
                                                         workload_mode):  # fmt: skip
    monkeypatch.setenv("FEATURE_RULED_OUT", probe_mode)
    monkeypatch.setenv("FEATURE_WORKLOAD", workload_mode)
    issue(world, 9)
    prim = primary_run(db, 9)
    pflags = json.loads(db.con.execute("SELECT flags FROM forecast_runs WHERE run_id = ?",
                                       [prim]).fetchone()[0])  # fmt: skip
    on = sorted(f for f, m in ((FEAT, probe_mode), ("workload", workload_mode))
                if m == "on")  # fmt: skip
    assert pflags["features_on"] == on  # the issued config, stated explicitly
    arms = runs(db, prim)
    arms = arms[arms["variant"] == "experiment"]
    for feat, mode in ((FEAT, probe_mode), ("workload", workload_mode)):
        if mode == "off":
            assert not any(feat in f["features_on"] for f in arms["flags"])
        else:
            assert len(_pairs(arms, feat)) == 1, (feat, mode, arms["flags"].tolist())


def test_the_null_intervention_arm_reproduces_the_issued_forecast(world, db):
    issue(world, 9, samples=320)
    prim = primary_run(db, 9)
    arms = runs(db, prim)
    control = arms[[f.get("role") == "control" for f in arms["flags"]]]
    assert len(control) == 1 and control["flags"].iloc[0]["samples"] == 320
    a = pit.run_projection(db, prim, SEASON, 9).set_index("player_id")["xpts"]
    b = pit.run_projection(db, control["run_id"].iloc[0], SEASON, 9).set_index("player_id")["xpts"]
    assert len(b) and np.abs(a.reindex(b.index) - b).max() == 0.0
    assert control["flags"].iloc[0]["null_check"]["passed"]


def test_feature_lifecycle_promotes_then_demotes_on_its_own_evidence(world, db, monkeypatch):
    monkeypatch.setitem(learning.THRESHOLDS, "min_gameweeks", 2)
    monkeypatch.setitem(learning.THRESHOLDS, "min_affected", 6)
    statuses = []
    for gw, right in ((6, True), (7, True), (8, False), (9, False), (10, False)):
        st = learning.current_state(db)
        before = st["config"]["features"][FEAT] if st else "shadow"
        rule_out(gw, starters(world, gw, played=not right))  # right: they really missed it
        issue(world, gw)
        finish(db, gw)
        res = learning.weekly_update(db, SEASON, gw)
        ev = json.loads(db.query("SELECT payload FROM reviews WHERE kind = 'evaluation' AND "
                                 "gw = ?", [gw])["payload"].iloc[0])  # fmt: skip
        cmp = ev["comparisons"][FEAT]
        assert cmp["valid"] and cmp["affected"], (gw, cmp)
        assert cmp["role"] == ("with" if before == "shadow" else "without")
        assert res.status in ("promoted", "retained", "unchanged"), res.reason
        st = learning.current_state(db)
        statuses.append((gw, st["config"]["features"][FEAT] if st else "shadow"))
    modes = dict(statuses)
    assert modes[7] == "on"  # two right weeks: promoted
    assert modes[10] == "shadow"  # wrong while on: the without-feature arm shows it, demoted


def test_brier_guard_uses_the_affected_cohort_only(world, db):
    players = starters(world, 9, played=False)
    rule_out(9, players)
    issue(world, 9)
    finish(db, 9)
    ev = learning.evaluate_gw(db, SEASON, 9)
    cmp = ev["comparisons"][FEAT]
    assert cmp["valid"]
    affected = {r["player_id"] for r in cmp["affected"]}
    assert affected and affected <= set(players)
    act = learning.actuals(db, SEASON, 9).set_index("player_id")
    fa = learning.run_frame(db, cmp["with"], SEASON, 9).set_index("player_id")
    fb = learning.run_frame(db, cmp["without"], SEASON, 9).set_index("player_id")
    single = [p for p in affected if act.loc[p, "fixtures"] == 1]
    y = act.loc[single, "started"].astype(float)
    want = float(((fa.loc[single, "p_start"] - y) ** 2 - (fb.loc[single, "p_start"] - y) ** 2)
                 .mean())  # fmt: skip
    assert cmp["start_brier_change"] == pytest.approx(want, abs=1e-9)
    feats = learning._feature_evidence(db, SEASON, 9, "shadow", FEAT)
    assert feats["start_brier_change"] == pytest.approx(want, abs=1e-9)


# ---- 3. eligibility of every arm, and the issued forecast the planner received ----------------
def _copy_run(db, run_id, **change):
    new = f"{run_id}-copy-{len(change)}-{sorted(change)[0]}"
    rows = db.query("SELECT * FROM projections WHERE run_id = ?", [run_id]).assign(run_id=new)
    db.append("projections", rows)
    meta = db.query("SELECT * FROM forecast_runs WHERE run_id = ?", [run_id]).iloc[0].to_dict()
    flags = json.loads(meta["flags"])
    flags.update(change.pop("flags", {}))
    meta.update({"run_id": new, "flags": json.dumps(flags), **change})
    db.upsert("forecast_runs", pd.DataFrame([meta]))
    return new


@pytest.mark.parametrize("change", [
    {"created_at": "late", "cutoff": "late", "eligible": False},
    {"kind": "backtest"},
    {"feature_version": "f-other"},
    {"flags": {"samples": 99}},
])  # fmt: skip
def test_an_invalid_arm_never_supplies_evidence(world, db, monkeypatch, change):
    monkeypatch.setitem(learning.THRESHOLDS, "min_gameweeks", 1)
    monkeypatch.setitem(learning.THRESHOLDS, "min_affected", 1)
    rule_out(9, starters(world, 9, played=False))
    issue(world, 9)
    finish(db, 9)
    prim = primary_run(db, 9)
    arms = runs(db, prim)
    with_feat = arms[[f.get("role") == f"with:{FEAT}" for f in arms["flags"]]]
    late = deadline(db, 9) + timedelta(days=1)
    change = {k: (late if v == "late" else v) for k, v in change.items()}
    bad = _copy_run(db, with_feat["run_id"].iloc[0], **change)
    db.con.execute("DELETE FROM forecast_runs WHERE run_id = ?", [with_feat["run_id"].iloc[0]])
    ev = learning.evaluate_gw(db, SEASON, 9)
    cmp = ev["comparisons"][FEAT]
    assert not cmp["valid"] and bad in json.dumps(ev["excluded"])
    res = learning.weekly_update(db, SEASON, 9)
    st = learning.current_state(db)
    assert not st or st["config"]["features"][FEAT] == "shadow", res.reason


def test_the_issued_forecast_scored_is_the_one_the_planner_received(world, db, monkeypatch):
    learning._store_state(db, "current", None, SEASON, 8, None,
                          {"calibration": {"MID": 1.06, "FWD": 0.95},
                           "features": {FEAT: "shadow", "workload": "shadow"}},
                          {}, {}, "test state")  # fmt: skip
    seen = {}
    real_decide = agent.decide

    def spy(state, proj, *a, **kw):
        seen["xp"] = dict(zip(proj.player_ids, proj.mean()[:, 0].astype(float), strict=True))
        return real_decide(state, proj, *a, **kw)

    core = [int(p) for p in world.pool.sort_values("price").player_id.tail(20)]
    monkeypatch.setattr(agent, "decide", spy)
    monkeypatch.setattr(agent, "_ownership", lambda *a: ({p: 60.0 for p in core},
                                                         {p: 0.9 for p in core}, "ownership: test"))  # fmt: skip
    mem = lessons.Memory(template=1.08, template_n=500)
    monkeypatch.setattr(agent.lessons, "memory", lambda *a: mem)
    issue(world, 9)
    finish(db, 9)
    ev = learning.evaluate_gw(db, SEASON, 9)
    act = learning.actuals(db, SEASON, 9).set_index("player_id")["actual"]
    xp = pd.Series(seen["xp"]).reindex(act.index).dropna()
    err = xp - act.reindex(xp.index)
    assert ev["issued"]["n"] == len(xp)
    assert ev["issued"]["mae"] == pytest.approx(float(err.abs().mean()), abs=1e-3)
    assert ev["issued"]["bias"] == pytest.approx(float(err.mean()), abs=1e-3)
    raw = pit.run_projection(db, primary_run(db, 9), SEASON, 9).set_index("player_id")["xpts"]
    assert not np.allclose(raw.reindex(xp.index), xp)  # both corrections really applied
    issued = runs(db, primary_run(db, 9))
    t = issued[issued["variant"] == "issued"]["flags"].iloc[0]["transformations"]
    assert t["calibration"] == {"MID": 1.06, "FWD": 0.95} and t["template"]["factor"] == 1.08


def test_a_provisional_week_changes_nothing(world, db):
    issue(world, 9)
    finish(db, 9, checked=False)
    res = learning.weekly_update(db, SEASON, 9)
    assert res.status == "not_final" and learning.current_state(db) is None
    assert db.con.execute("SELECT count(*) FROM evaluations").fetchone()[0] == 0


def test_post_deadline_runs_are_never_the_issued_forecast(world, db):
    issue(world, 9)
    first = primary_run(db, 9)
    issue(world, 9, now=deadline(db, 9) + timedelta(hours=1))  # a late re-run
    finish(db, 9)
    assert learning.evaluate_gw(db, SEASON, 9)["primary_run"] == first


def test_insufficient_evidence_retains_the_current_state(world, db):
    for gw in (8, 9):
        rule_out(gw, starters(world, gw, played=False))
        issue(world, gw)
        finish(db, gw)
        res = learning.weekly_update(db, SEASON, gw)
    assert res.status == "retained" and "insufficient evidence" in res.reason
    assert learning.current_state(db) is None


def test_missing_holdout_metrics_never_authorise_a_calibration_change(world, db, monkeypatch):
    _underpredicted_weeks(world)
    monkeypatch.setattr(learning, "_holdout_mae", lambda *a, **k: None)
    res = learning.weekly_update(db, SEASON, 9)
    assert learning.current_state(db) is None and res.status in ("retained", "rejected")
    assert "holdout" in res.reason


def test_calibration_candidate_worse_on_the_holdout_is_rejected(world, db, monkeypatch):
    _underpredicted_weeks(world)
    monkeypatch.setitem(learning.THRESHOLDS, "max_regression", -10.0)  # demand a huge gain
    res = learning.weekly_update(db, SEASON, 9)
    assert res.status == "rejected" and learning.current_state(db) is None
    assert learning.history(db)["status"].tolist() == ["rejected"]
