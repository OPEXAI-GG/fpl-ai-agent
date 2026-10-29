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
        self.calls, self.boot_gets, self.boot_parses = [], 0, 0

    def bootstrap(self, raw=None):
        self.boot_parses += 1
        ev = SimpleNamespace(id=GW, is_next=True, deadline_time=self.deadline)
        return SimpleNamespace(events=[ev])

    def bootstrap_raw(self):
        self.boot_gets += 1
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


def fake(env, hours=3.0):
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
    w.db.con.execute("DELETE FROM runs")
    run_weekly(w.db, Settings(), opts(notify=True, force=True), fpl=fake(env))
    run_weekly(w.db, Settings(), opts(notify=True, force=True), fpl=fake(env))
    assert len(sent) == 1


def test_a_failed_move_makes_the_run_exit_nonzero():
    from fpl_agent.agent import RunResult
    from fpl_agent.execution.execute import Outcome

    res = RunResult("ok")
    assert res.exit_code == 0
    res.outcome = Outcome(mode="live", errors=["FPL sign-in failed"])
    assert res.exit_code == 2


def test_a_forced_run_that_failed_its_moves_still_notifies(env, monkeypatch):
    from fpl_agent.execution.execute import Outcome

    sent = []
    monkeypatch.setattr("fpl_agent.agent.notify.send", lambda m, *a, **k: sent.append(m) or True)
    w = env[0]
    w.db.con.execute("DELETE FROM decisions")
    w.db.con.execute("DELETE FROM runs")
    run_weekly(w.db, Settings(), opts(notify=True, force=True), fpl=fake(env))  # first: notifies
    monkeypatch.setattr(
        "fpl_agent.agent.execute", lambda *a, **k: Outcome(mode="live", errors=["sign-in failed"])
    )
    run_weekly(w.db, Settings(), opts(notify=True, force=True), fpl=fake(env))
    assert len(sent) == 2 and "sign-in failed" in sent[1]


def test_live_moves_are_never_sent_when_the_current_team_cannot_be_read(env, monkeypatch):
    from fpl_agent.execution.auth import AuthError

    called = []
    monkeypatch.setattr("fpl_agent.agent.execute", lambda *a, **k: called.append(1))

    class Dead:
        def exchange(self):
            raise AuthError("FPL sign-in failed (HTTP 400 invalid_grant)")

    w = env[0]
    res = run_weekly(w.db, Settings(mode="live"), opts(force=True), fpl=fake(env),
                     exchange=Dead)  # fmt: skip
    assert not called and res.outcome.errors and res.exit_code == 2
    assert any("sign-in failed" in n for n in res.notes)


def test_hand_changes_after_the_bots_last_moves_are_found(env):
    import pandas as pd

    from fpl_agent.agent import DONE, manual_changes
    from fpl_agent.solver.planner import SquadPlayer

    w = env[0]
    squad = [SquadPlayer(1000 + i, "MID", 1, 50, 50) for i in range(15)]
    state = SimpleNamespace(squad=squad)
    assert manual_changes(w.db, SEASON, 37, state) == []  # no completed run: nothing to compare
    planned = [p.player_id for p in squad[:14]] + [999]
    w.db.append("decisions", pd.DataFrame([{
        "season": SEASON, "gw": 37, "created_at": datetime.now(UTC), "mode": DONE,
        "payload": json.dumps({"xi": planned[:11], "bench": planned[11:]}),
    }]))  # fmt: skip
    assert manual_changes(w.db, SEASON, 37, state) == [1014]


def test_scheduled_run_holds_moves_for_team_news(env, monkeypatch):
    from fpl_agent import agent

    seen = []

    def hold(ins, outs, moving, hours, update, name):
        seen.append(hours)
        return True, "moves held for team news"

    monkeypatch.setattr(agent.timing, "hold_moves", hold)
    res = run_weekly(env[0].db, Settings(), opts(), fpl=fake(env, hours=20))
    assert res.status == "ok" and 19 < seen[0] < 21
    # transfers wait; the squad already owned still gets its lineup and captain (TASK A06)
    assert not any("transfer" in x for x in res.outcome.done)
    assert any("captain" in x for x in res.outcome.done)
    assert "moves held for team news" in res.report
    seen.clear()
    res = run_weekly(env[0].db, Settings(), opts(force=True), fpl=fake(env, hours=20))
    assert seen == [] and res.outcome.done  # a hand-started run acts now


def test_the_plan_reads_the_lessons_memory(env):
    from fpl_agent import lessons

    w = env[0]
    lessons.save(w.db, SEASON, GW - 1, [lessons.Mistake("hand_transfers", 3, "w", "move now",
                                                        "act_now")])  # fmt: skip
    try:
        res = run_weekly(w.db, Settings(), opts(force=True), fpl=fake(env))
    finally:
        w.db.con.execute("DELETE FROM lessons")
    assert res.status == "ok", res.notes
    assert any(n.startswith("memory: act now (weight 1.00)") for n in res.notes)
    assert any("lesson from GW9: move now" in n for n in res.notes)


@pytest.mark.parametrize("held,verified,done", [
    (True, None, False),  # held for team news: nothing was sent
    (False, False, False),  # sent, but FPL's read-back did not match
    (False, None, False),  # nothing could be checked
    (False, True, True),
])  # fmt: skip
def test_only_a_verified_live_run_is_recorded_as_done(env, monkeypatch, held, verified, done):
    from dataclasses import replace as dc_replace

    from fpl_agent import agent
    from fpl_agent.execution.execute import Outcome

    monkeypatch.setattr(agent, "read_live", lambda state, *a, **k: (
        dc_replace(state), object(), False, "live team read"))  # fmt: skip
    monkeypatch.setattr(agent.timing, "hold_moves", lambda *a: (held, "held" if held else ""))
    monkeypatch.setattr(agent, "execute", lambda *a, **k: Outcome(
        "live", done=["set captain"], verified=verified))  # fmt: skip
    w = env[0]
    w.db.con.execute("DELETE FROM decisions")
    w.db.con.execute("DELETE FROM runs")
    run_weekly(w.db, Settings(mode="live"), opts(), fpl=fake(env))
    modes = {r[0] for r in w.db.con.execute("SELECT mode FROM decisions").fetchall()}
    assert (agent.DONE in modes) is done


def test_after_a_free_hit_the_plan_starts_from_the_squad_before_it(env):
    class AfterFreeHit(FakeFPL):
        def entry_picks_raw(self, entry, gw):
            self.calls.append(gw)
            return super().entry_picks_raw(entry, gw)

        def entry_history_raw(self, entry):
            return {"chips": [{"name": "freehit", "event": GW - 1}], "current": []}

    f = AfterFreeHit(env[1], env[2], datetime.now(UTC) + timedelta(hours=3))
    res = run_weekly(env[0].db, Settings(), opts(), fpl=f)
    assert res.status == "ok" and GW - 2 in f.calls and GW - 1 not in f.calls


def test_a_plan_fetches_the_bootstrap_once(env):
    f = fake(env)
    res = run_weekly(env[0].db, Settings(), opts(), fpl=f)
    assert res.status == "ok" and f.boot_gets == 1


def test_the_client_parses_an_already_fetched_bootstrap_without_a_request():
    import httpx

    from fpl_agent.data.fpl_client import FPLClient
    from tests.helpers import fixture

    def no_network(request):
        raise AssertionError("no request expected")

    c = FPLClient(client=httpx.Client(transport=httpx.MockTransport(no_network)))
    assert c.bootstrap(fixture("bootstrap-static.json")).events


def _attempt(db, source, ok, at, critical):
    db.con.execute("INSERT INTO source_attempts VALUES (?, 1, 'data update', ?, ?, ?, NULL, ?, "
                   "?, ?, ?)", [f"t-{source}-{at.isoformat()}", source, ok, critical,
                                None if ok else "HTTP 503", at, at, 0 if ok else 2])  # fmt: skip


@pytest.mark.parametrize("case", ["critical_after", "critical_before", "optional"])
def test_a_critical_source_failing_after_the_snapshot_makes_the_plan_diagnostic(env, case):
    """End to end with a fake FPL, dry: a critical refresh that failed after the snapshot the
    plan uses is a rail in every mode; the plan is reported as a diagnostic (exit 3, run
    blocked), not a recommendation. A failure older than the snapshot, or of an optional
    source, only adds a note."""
    w = env[0]
    snap = w.db.con.execute("SELECT max(snapshot_at) FROM fpl_players_gw WHERE season = ? "
                            "AND gw = ?", [SEASON, GW]).fetchone()[0]  # fmt: skip
    now = datetime.now(UTC).replace(tzinfo=None)
    started = now - timedelta(seconds=1)  # decisions.created_at is naive UTC too
    try:
        for src in ("fpl:bootstrap", "fpl:fixtures", "fpl:players"):
            _attempt(w.db, src, True, snap - timedelta(days=2), True)
        if case == "critical_after":
            _attempt(w.db, "fpl:players", False, now - timedelta(hours=1), True)
        elif case == "critical_before":
            _attempt(w.db, "fpl:players", False, snap - timedelta(days=1), True)
        else:
            _attempt(w.db, "understat:league", False, now - timedelta(hours=1), False)
        res = run_weekly(w.db, Settings(), opts(force=True), fpl=fake(env))
        assert res.status == "ok" and res.outcome.mode == "dry", res.notes
        if case == "critical_after":
            assert res.data_blocked and res.exit_code == 3 and res.run_status == "blocked"
            assert any("critical_source_failed" in b and "fpl:players" in b
                       for b in res.outcome.blocked)  # fmt: skip
            assert "DIAGNOSTIC ONLY, NOT A RECOMMENDATION" in res.report.splitlines()[0]
            assert not res.outcome.done  # nothing presented as "would ..."
        else:
            assert not res.data_blocked and res.exit_code == 0, res.outcome.blocked
            assert "DIAGNOSTIC" not in res.report
            if case == "optional":
                assert any("understat:league failed" in n for n in res.notes)
    finally:
        w.db.con.execute("DELETE FROM source_attempts")
        w.db.con.execute("DELETE FROM decisions WHERE created_at >= ?", [started])
