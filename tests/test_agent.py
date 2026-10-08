"""The weekly run end to end on the synthetic world and a fake FPL: gate, squad, projection,
decision, rails, dry execution, recording, report."""

import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from fpl_agent import review
from fpl_agent.agent import RunOptions, run_weekly
from fpl_agent.config import Settings
from fpl_agent.model import project
from fpl_agent.scout import Scout
from tests.synth import build_world

GW, SEASON = 10, 2024


class FakeFPL:
    def __init__(self, pool, squad_ids, deadline):
        self.pool, self.squad, self.deadline = pool, squad_ids, deadline
        self.calls = []

    def bootstrap(self):
        ev = SimpleNamespace(id=GW, is_next=True, deadline_time=self.deadline)
        return SimpleNamespace(events=[ev])

    def bootstrap_raw(self):
        codes = {"GKP": 1, "DEF": 2, "MID": 3, "FWD": 4}
        return {
            "elements": [
                {
                    "id": int(r.fpl_id),
                    "element_type": codes[r.position],
                    "team": int(r.team_id),
                    "now_cost": int(r.price),
                    "cost_change_start": 0,
                }
                for r in self.pool.itertuples()
            ]
        }

    def entry_picks_raw(self, entry, gw):
        self.calls.append("picks")
        return {
            "picks": [{"element": i, "position": n + 1} for n, i in enumerate(self.squad)],
            "entry_history": {"bank": 30, "value": 1000},
        }

    def entry_transfers_raw(self, entry):
        return []

    def entry_history_raw(self, entry):
        return {"chips": [{"name": "3xc", "event": 2}], "current": []}


@pytest.fixture(scope="module")
def env():
    w = build_world(n_teams=10, seasons=(2023, 2024), seed=5)
    pool = project.current_pool(w.db, SEASON, GW)
    need, per_club, squad = {"GKP": 2, "DEF": 5, "MID": 5, "FWD": 3}, {}, []
    for r in pool.sort_values("price").itertuples():
        if need[r.position] and per_club.get(r.team_id, 0) < 2:
            squad.append(int(r.fpl_id))
            need[r.position] -= 1
            per_club[r.team_id] = per_club.get(r.team_id, 0) + 1
    assert len(squad) == 15
    return w, pool, squad


@pytest.fixture(autouse=True)
def no_keys(monkeypatch):
    for k in ("ANTHROPIC_API_KEY", "ODDS_API_KEY", "AGENT_MODE", "AGENT_ENABLED"):
        monkeypatch.delenv(k, raising=False)


def fake(env, hours=10.0):
    return FakeFPL(env[1], env[2], datetime.now(UTC) + timedelta(hours=hours))


def opts(**kw):
    return RunOptions(season=SEASON, horizon=3, entry_id=1, samples=150, **kw)


def test_dry_run_produces_a_recorded_decision_and_a_report(env, tmp_path):
    w = env[0]
    res = run_weekly(w.db, Settings(), opts(out_dir=tmp_path), fpl=fake(env))
    assert res.status == "ok" and res.gw == GW, res.notes
    assert res.outcome.mode == "dry" and res.outcome.ok
    assert all(x.startswith("would ") for x in res.outcome.done)
    assert "DRY mode" in res.report and "captain" in res.report
    assert any("scout skipped" in n for n in res.notes) and any(
        "odds skipped" in n for n in res.notes
    )
    saved = json.loads((tmp_path / f"gw{GW}.json").read_text())
    assert saved["mode"] == "dry" and len(saved["decision"]["xi"]) == 11
    assert (tmp_path / f"gw{GW}.md").exists()
    row = w.db.con.execute("SELECT mode FROM decisions WHERE gw = ?", [GW]).fetchone()
    assert row == ("dry",)
    assert w.db.con.execute("SELECT count(*) FROM projections WHERE gw = ?", [GW]).fetchone()[0] > 0
    assert review.decision_payload(res.decision)["gw"] == GW


def test_run_outside_the_action_window_is_skipped(env):
    res = run_weekly(env[0].db, Settings(), opts(), fpl=fake(env, hours=100))
    assert res.status == "skipped" and "outside the action window" in res.report


def test_kill_switch_makes_the_run_a_no_op(env):
    f = fake(env)
    res = run_weekly(env[0].db, Settings(enabled=False), opts(force=True), fpl=f)
    assert res.status == "skipped" and f.calls == []


def test_missing_entry_id_is_a_clear_error(env):
    res = run_weekly(env[0].db, Settings(), RunOptions(season=SEASON, force=True), fpl=fake(env))
    assert res.status == "error" and res.exit_code == 2 and "FPL_ENTRY_ID" in res.report


def test_fpl_outage_is_an_error_not_a_crash(env):
    class Down:
        def bootstrap(self):
            raise RuntimeError("down")

    res = run_weekly(env[0].db, Settings(), opts(force=True), fpl=Down())
    assert res.status == "error" and "bootstrap" in res.report


def test_unknown_squad_player_is_reported(env):
    f = fake(env)
    f.squad = [*f.squad[:14], 999999]
    f.pool = f.pool  # the bootstrap does not know him either
    res = run_weekly(env[0].db, Settings(), opts(force=True), fpl=f)
    assert res.status == "error"


def test_scout_without_key_is_inert(env):
    assert not Scout().enabled


def test_a_second_run_in_the_same_window_does_not_notify_again(env, monkeypatch):
    sent = []
    monkeypatch.setattr("fpl_agent.agent.notify.send", lambda m, *a, **k: sent.append(m) or True)
    w = env[0]
    w.db.con.execute("DELETE FROM decisions")
    run_weekly(w.db, Settings(), opts(notify=True, force=True), fpl=fake(env))
    run_weekly(w.db, Settings(), opts(notify=True, force=True), fpl=fake(env))
    assert len(sent) == 1
