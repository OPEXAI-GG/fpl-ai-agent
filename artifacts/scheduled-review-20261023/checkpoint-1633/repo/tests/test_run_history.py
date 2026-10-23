"""Every weekly run leaves a record however it ends; reports are re-sent until delivered and
alerts are not repeated; live runs refuse stale data; held runs still set the lineup; and a
deadline with no good run raises the alarm."""

import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import httpx
import pandas as pd
import pytest

from fpl_agent import agent, notify, runs
from fpl_agent.agent import run_weekly, write_outputs
from fpl_agent.config import Settings, kill_switch_enabled
from fpl_agent.db.database import FootballDB
from fpl_agent.execution import safety
from fpl_agent.execution.execute import Outcome
from tests.test_agent import FakeFPL, env, fake, no_keys, opts  # noqa: F401  (fixtures)
from tests.test_execution import RULES, base, ctx_for, rules_hit  # noqa: F401  (fixture)


def rows(db):
    return db.query("SELECT * FROM runs ORDER BY started_at")


def values(series):
    """A column as plain Python values, missing ones as None (pandas uses NA/NaN)."""
    return [None if pd.isna(v) else v for v in series.tolist()]


@pytest.fixture
def fresh(env):  # noqa: F811
    db = env[0].db
    db.con.execute("DELETE FROM runs")
    db.con.execute("DELETE FROM decisions")
    return db


@pytest.fixture
def sent(monkeypatch):
    out = []
    monkeypatch.setattr(agent.notify, "send", lambda m, *a, **k: out.append(m) or True)
    return out


def test_every_way_a_run_ends_is_recorded(env, fresh, monkeypatch):  # noqa: F811
    run_weekly(fresh, Settings(enabled=False), opts())
    run_weekly(fresh, Settings(), opts(), fpl=fake(env, hours=60))  # outside the window
    no_entry = agent.RunOptions(season=opts().season, entry_id=None, force=True)
    run_weekly(fresh, Settings(), no_entry, fpl=fake(env))

    class Down(FakeFPL):
        def bootstrap_raw(self):
            raise httpx.ConnectError("down")

    run_weekly(fresh, Settings(), opts(), fpl=Down(env[1], env[2], datetime.now(UTC)))
    run_weekly(fresh, Settings(), opts(force=True), fpl=fake(env))  # a full dry plan
    monkeypatch.setattr(agent.project, "current_pool", lambda *a, **k: 1 / 0)
    crashed = run_weekly(fresh, Settings(), opts(force=True), fpl=fake(env))
    r = rows(fresh)
    assert values(r.status) == ["skipped", "skipped", "error", "error", "ok", "error"]
    assert values(r.state)[-2:] == ["planned", "none"]
    assert crashed.exit_code == 2 and "ZeroDivisionError" in crashed.report
    ok = r.iloc[4]
    assert isinstance(ok.projection_run, str) and not pd.isna(ok.decision_at)
    assert not pd.isna(ok.snapshot_at)
    stages = json.loads(ok.stages)
    assert {"bootstrap", "squad", "model", "execute"} <= set(stages)
    assert all(v["status"] == "ok" for v in stages.values())


def test_a_report_that_failed_to_send_is_sent_by_the_next_run(env, fresh, monkeypatch):  # noqa: F811
    attempts = []
    monkeypatch.setattr(agent.notify, "send", lambda m, *a, **k: (attempts.append(m), False)[1])
    run_weekly(fresh, Settings(), opts(notify=True), fpl=fake(env))
    monkeypatch.setattr(agent.notify, "send", lambda m, *a, **k: attempts.append(m) or True)
    run_weekly(fresh, Settings(), opts(notify=True), fpl=fake(env))
    run_weekly(fresh, Settings(), opts(notify=True), fpl=fake(env))
    assert len(attempts) == 2  # failed, retried and delivered, then not repeated
    assert values(rows(fresh).delivered) == [False, True, None]


def test_a_held_run_still_sets_the_lineup_but_no_transfers(env, fresh, monkeypatch):  # noqa: F811
    monkeypatch.setattr(agent.timing, "hold_moves", lambda *a: (True, "held for team news"))
    res = run_weekly(fresh, Settings(), opts(), fpl=fake(env, hours=20))
    assert any("captain" in x for x in res.outcome.done)
    assert not any("transfer" in x for x in res.outcome.done)
    assert rows(fresh).status.tolist() == ["held"]


def test_live_blocks_fail_the_run_and_alert_once_per_reason(env, fresh, sent, monkeypatch):  # noqa: F811
    from dataclasses import replace as dc_replace

    monkeypatch.setattr(agent, "read_live", lambda state, *a, **k: (
        dc_replace(state), object(), False, "live team read"))  # fmt: skip
    blocked = Outcome("live", blocked=["[global] stale_snapshot: FPL data is 9.0h old"])
    monkeypatch.setattr(agent, "execute", lambda *a, **k: blocked)
    first = run_weekly(fresh, Settings(mode="live"), opts(notify=True), fpl=fake(env))
    second = run_weekly(fresh, Settings(mode="live"), opts(notify=True), fpl=fake(env))
    assert first.exit_code == 3 and second.exit_code == 3
    assert rows(fresh).status.tolist() == ["blocked", "blocked"]
    assert len(sent) == 1 and "stale_snapshot" in sent[0]


def test_stale_or_unknown_snapshots_block_live_writes_only(base):  # noqa: F811
    d = base[2]
    for age, blocked in ((1.0, False), (7.0, True), (None, True)):
        v = safety.check(d, ctx_for(base, mode="live", snapshot_age_hours=age), RULES)
        assert rules_hit(v, "stale_snapshot") is blocked, age
        assert v.blocked("transfers") is blocked and v.blocked("lineup") is blocked
    dry = safety.check(d, ctx_for(base, snapshot_age_hours=None), RULES)
    assert not rules_hit(dry, "stale_snapshot")  # dry reports are never blocked by age


@pytest.mark.parametrize("raw,enabled", [
    (None, True), ("", True), ("true", True), ("1", True), ("YES", True), ("on", True),
    ("false", False), ("0", False), ("no", False), ("Off", False), ("flase", False),
    ("maybe", False),
])  # fmt: skip
def test_kill_switch_parser(raw, enabled, monkeypatch):
    assert kill_switch_enabled(raw) is enabled
    if raw is None:
        monkeypatch.delenv("AGENT_ENABLED", raising=False)
    else:
        monkeypatch.setenv("AGENT_ENABLED", raw)
    monkeypatch.setenv("AGENT_MODE", "live")
    assert Settings.from_env().is_live is enabled  # a typo can never enable writes


def test_notify_retries_then_reports_failure(monkeypatch):
    monkeypatch.setattr(notify, "_sleep", lambda s: None)
    replies = iter([503, 200])
    ok = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(next(replies))))
    assert notify.send("hello", client=ok, webhook="https://example.invalid/x") is True
    down = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(503)))
    assert notify.send("hello", client=down, webhook="https://example.invalid/x") is False
    calls = []

    def second_chunk_fails(r):
        calls.append(1)
        return httpx.Response(200 if len(calls) == 1 else 400)

    split = httpx.Client(transport=httpx.MockTransport(second_chunk_fails))
    long = "\n".join(["x" * 1000] * 3)
    assert notify.send(long, client=split, webhook="https://example.invalid/x") is False


def seed_run(db, gw, status, state, ended):
    db.upsert("runs", pd.DataFrame([{"run_id": f"{gw}{status}{state}{ended:%H%M}", "season": 2026,
                                     "gw": gw, "entry_id": 1, "status": status, "state": state,
                                     "ended_at": ended}]))  # fmt: skip


def test_missed_runs_raise_the_deadline_alarm():
    db = FootballDB()
    at = datetime(2026, 10, 24, 15, tzinfo=UTC)
    deadline = at + timedelta(hours=2)
    assert runs.coverage(db, 2026, 9, 1, deadline, live=False, at=at).alarm  # nothing ran
    assert not runs.coverage(db, 2026, 9, 1, at + timedelta(hours=10), False, at=at).alarm
    assert not runs.coverage(db, 2026, 9, 1, at - timedelta(hours=1), False, at=at).alarm
    seed_run(db, 9, "skipped", "none", datetime(2026, 10, 24, 12))
    cov = runs.coverage(db, 2026, 9, 1, deadline, live=False, at=at)
    assert cov.alarm and "skipped" in cov.message
    seed_run(db, 9, "ok", "planned", datetime(2026, 10, 24, 13))
    assert not runs.coverage(db, 2026, 9, 1, deadline, live=False, at=at).alarm
    assert runs.coverage(db, 2026, 9, 1, deadline, live=True, at=at).alarm  # live needs verified
    seed_run(db, 9, "ok", "verified", datetime(2026, 10, 24, 14))
    assert not runs.coverage(db, 2026, 9, 1, deadline, live=True, at=at).alarm


def test_a_run_that_stopped_early_never_replaces_the_published_plan(tmp_path):
    res = agent.RunResult("skipped", gw=9, report="status: skipped")
    write_outputs(tmp_path, res, "dry")
    assert [p.name for p in tmp_path.iterdir()] == ["run-skipped.md"]


def test_state_names():
    assert runs.state_of(None, None) == "none"
    d = SimpleNamespace()
    assert runs.state_of(d, Outcome("dry", done=["would x"])) == "planned"
    assert runs.state_of(d, Outcome("live", done=["x"])) == "applied"
    assert runs.state_of(d, Outcome("live", done=["x"], verified=True)) == "verified"
    assert runs.state_of(d, Outcome("live", done=["x"], errors=["e"])) == "planned"
