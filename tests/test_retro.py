"""The retro: today's agent on one gameweek from the submitted squad, against the submitted team."""

import pytest

from fpl_agent.cli import main
from fpl_agent.model import project, replay, retro
from tests.synth import build_world

GW, SEASON, ENTRY = 10, 2024, 7


class FakeFPL:
    def __init__(self, pool, squad, deadline="2100-01-01T10:00:00Z"):
        self.pool, self.squad, self.deadline = pool, squad, deadline

    def bootstrap_raw(self):
        codes = {"GKP": 1, "DEF": 2, "MID": 3, "FWD": 4}
        return {
            "events": [{"id": GW, "deadline_time": self.deadline}],
            "elements": [
                {"id": int(r.fpl_id), "element_type": codes[r.position], "team": int(r.team_id),
                 "now_cost": int(r.price), "cost_change_start": 0}
                for r in self.pool.itertuples()
            ],
        }  # fmt: skip

    def entry_picks_raw(self, entry, gw):
        return {
            "picks": [
                {"element": i, "position": n + 1, "is_captain": n == 8, "is_vice_captain": n == 9}
                for n, i in enumerate(self.squad)
            ],
            "entry_history": {"bank": 30, "value": 1000, "points": 50, "event_transfers_cost": 0},
            "active_chip": None,
        }

    def entry_transfers_raw(self, entry):
        return [{"event": GW, "time": "2024-01-01T00:00:00Z", "element_in": self.squad[0],
                 "element_out": self.squad[1], "element_in_cost": 50}]  # fmt: skip

    def entry_history_raw(self, entry):
        return {"chips": [{"name": "3xc", "event": 2}], "current": []}

    def live_raw(self, gw):
        return {"elements": [{"id": int(r.fpl_id),
                              "stats": {"total_points": int(r.fpl_id) % 7, "minutes": 90}}
                             for r in self.pool.itertuples()]}  # fmt: skip


@pytest.fixture(scope="module")
def env():
    w = build_world(n_teams=10, seasons=(2023, 2024), seed=5)
    pool = project.current_pool(w.db, SEASON, GW)
    by_pos = {k: [] for k in ("GKP", "DEF", "MID", "FWD")}
    per_club, need = {}, {"GKP": 2, "DEF": 5, "MID": 5, "FWD": 3}
    for r in pool.sort_values("price").itertuples():
        if len(by_pos[r.position]) < need[r.position] and per_club.get(r.team_id, 0) < 2:
            by_pos[r.position].append(int(r.fpl_id))
            per_club[r.team_id] = per_club.get(r.team_id, 0) + 1
    g, d, m, f = (by_pos[k] for k in ("GKP", "DEF", "MID", "FWD"))
    squad = [g[0], *d[:4], *m[:4], *f[:2], g[1], d[4], m[4], f[2]]  # a 4-4-2 and its bench
    assert len(squad) == 15
    return w, pool, squad


def cfg():
    return replay.ReplayConfig(horizon=2, samples=80, use_chips=False)


def test_retro_sets_the_agent_against_the_submitted_team(env):
    w, pool, squad = env
    text = retro.run(w.db, SEASON, GW, ENTRY, cfg(), fpl=FakeFPL(pool, squad))
    assert text.startswith(f"GW{GW} retro")
    for part in ("points after hits: you", "(FPL says 50)", "captain: you", "your moves:",
                 "agent moves:", "players only in your XI", "your XI:", "agent XI:"):  # fmt: skip
        assert part in text, text
    assert "out " in text  # the submitted transfer is listed with its points


def test_retro_says_so_when_no_snapshot_precedes_the_deadline(env):
    w, pool, squad = env
    fpl = FakeFPL(pool, squad, deadline="1990-01-01T10:00:00Z")
    assert "no player snapshot" in retro.run(w.db, SEASON, GW, ENTRY, cfg(), fpl=fpl)


def test_the_submitted_team_keeps_its_bench_order_with_the_goalkeeper_last(env):
    _, pool, squad = env
    fpl = FakeFPL(pool, squad)
    to_pid = dict(zip(pool["fpl_id"].astype(int), pool["player_id"].astype(int), strict=True))
    wk = retro.my_week(fpl.entry_picks_raw(ENTRY, GW), to_pid)
    assert len(wk.xi) == 11 and wk.bench[-1] == to_pid[squad[11]]
    assert wk.captain == to_pid[squad[8]] and wk.vice == to_pid[squad[9]]


def test_retro_command_needs_a_team_id(monkeypatch, capsys):
    monkeypatch.delenv("FPL_ENTRY_ID", raising=False)
    assert main(["retro", "--gw", "6"]) == 2
    assert "FPL_ENTRY_ID" in capsys.readouterr().out
