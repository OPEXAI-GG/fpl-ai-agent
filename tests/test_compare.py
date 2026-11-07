"""This week's transfer count compared through the planner: every count from rolling to the
planner's maximum, identical inputs, the existing policy, and faithful reporting."""

from dataclasses import replace
from types import SimpleNamespace

import numpy as np
import pytest

from fpl_agent.solver import chips as chips_mod
from fpl_agent.solver import compare
from fpl_agent.solver.decide import decide
from fpl_agent.solver.inputs import build_candidates, prune
from fpl_agent.solver.planner import PlanInput
from tests.test_decide import CFG, RULES, projection, state_for


@pytest.fixture(scope="module")
def world():
    proj, cands = projection(3)
    st = state_for(cands, bank=50, ft=2)
    return proj, st


def _base(proj, st):
    cands = prune(build_candidates(proj, st.squad), st.squad)
    return PlanInput(gws=list(proj.gws), squad=st.squad, bank=st.bank,
                     free_transfers=st.free_transfers, candidates=cands)  # fmt: skip


def scored(by_count):
    """A stand-in for the sampled-world scoring: worlds depend on the week's transfer count."""

    def score(plan, proj, rules, worlds):
        k = len(plan.first.transfers_in)
        return np.asarray(by_count(k, len(worlds)), dtype=float)

    return score


def test_every_count_up_to_the_planners_maximum_is_solved_with_that_count(world):
    proj, st = world
    base = _base(proj, st)
    cmp = compare.compare_counts(base, proj, RULES, CFG, 0.55, 1.0, 60)
    assert [o.transfers for o in cmp.options] == list(range(0, 2 + CFG.max_hits_per_gw + 1))
    for o in cmp.options:
        if o.plan is not None:
            assert len(o.plan.first.transfers_in) == o.transfers
            assert o.plan.first.hits == max(0, o.transfers - 2)
    roll = cmp.options[0]
    assert roll.plan.first.transfers_in == [] and roll.passes
    rows = cmp.rows()
    assert {"week_xp", "horizon_xp", "objective", "win_share", "gain", "passes"} <= set(rows[1])


def test_a_single_transfer_is_chosen_when_the_preferred_multi_transfer_fails(world, monkeypatch):
    proj, st = world
    preferred = decide(st, proj, RULES, cfg=CFG, use_chips=False)
    full = compare.compare_counts(_base(proj, st), proj, RULES, CFG, 0.55, 1.0, 60)
    best = max((o for o in full.options if o.plan), key=lambda o: o.objective)
    assert best.transfers >= 2 and preferred.comparison  # the planner wants more than one move

    def worlds(k, n):  # one move wins everywhere; more than one only on a few lucky worlds
        if k == 0:
            return np.zeros(n)
        if k == 1:
            return np.full(n, 2.0)
        return np.where(np.arange(n) < n // 5, 10.0, -2.0)

    monkeypatch.setattr(chips_mod, "score_plan", scored(worlds))
    d = decide(st, proj, RULES, cfg=CFG, use_chips=False)
    assert len(d.transfers_in) == 1
    rows = {r["transfers"]: r for r in d.comparison}
    assert rows[1]["passes"] and not rows[best.transfers]["passes"]
    assert "fails robustness" in rows[best.transfers]["reason"]
    assert any(w.startswith("held ") for w in d.warnings)
    assert any("chose 1 transfer(s)" in w for w in d.warnings)


def test_when_every_option_fails_the_transfer_is_rolled(world, monkeypatch):
    proj, st = world
    monkeypatch.setattr(chips_mod, "score_plan",
                        scored(lambda k, n: np.where(np.arange(n) < n // 4, 1.0, -1.0) * (k > 0)))  # fmt: skip
    d = decide(st, proj, RULES, cfg=CFG, use_chips=False)
    assert d.transfers_in == [] and d.hits == 0
    assert not any(r["passes"] for r in d.comparison if r["transfers"] > 0)
    assert any(w.startswith("held ") for w in d.warnings)


def test_thresholds_are_not_relaxed_for_smaller_moves(world, monkeypatch):
    proj, st = world  # 54% of worlds and +0.9: just below both thresholds for every count
    monkeypatch.setattr(chips_mod, "score_plan", scored(
        lambda k, n: (np.where(np.arange(n) < round(0.54 * n), 1.0, -0.2) * (k > 0))))  # fmt: skip
    d = decide(st, proj, RULES, cfg=CFG, use_chips=False)
    assert d.transfers_in == []


def test_the_comparison_ignores_the_order_of_its_inputs(world):
    proj, st = world
    base = _base(proj, st)
    shuffled = replace(base, candidates=list(reversed(base.candidates)),
                       squad=list(reversed(base.squad)))  # fmt: skip
    a = compare.compare_counts(base, proj, RULES, CFG, 0.55, 1.0, 60).rows()
    b = compare.compare_counts(shuffled, proj, RULES, CFG, 0.55, 1.0, 60).rows()
    assert a == b


def test_no_comparison_when_this_weeks_transfers_are_already_made(world):
    proj, st = world
    d = decide(replace(st, already_transferred=1), proj, RULES, cfg=CFG, use_chips=False)
    assert d.comparison == [] and "already made" in d.comparison_note


def test_uncertain_free_transfers_make_differing_advice_conditional():
    from fpl_agent.team.report import scenario_lines

    team = SimpleNamespace(free_transfers=SimpleNamespace(status="inferred"))
    two = SimpleNamespace(chip=None, transfers_in=[5, 6], transfers_out=[1, 2], hits=0, captain=9)
    one = SimpleNamespace(chip=None, transfers_in=[5], transfers_out=[1], hits=0, captain=9)
    lines, conditional = scenario_lines(3, two, [(1, one)], team, {})
    assert conditional and any("CONDITIONAL" in x for x in lines)
    assert any("with 3 free" in x for x in lines) and any("with 1 free" in x for x in lines)
    same, cond2 = scenario_lines(3, one, [(1, one)], team, {})
    assert not cond2 and any("holds for every balance" in x for x in same)


def test_a_hold_resting_on_an_unfinished_chip_scenario_is_marked_unproven():
    sc = chips_mod.Scenario("freehit", 12, None, gain=3.0, optimal=False, max_gain=11.0)
    done = chips_mod.Scenario("freehit", 11, None, gain=2.0)
    held = {"freehit": "best gain +3.0 below the 8-point threshold"}
    chips_mod._unproven(held, [sc, done], {"freehit": (10, 19)}, 17, chips_mod.ChipConfig())
    assert "not proven" in held["freehit"] and "GW12" in held["freehit"]
    assert "up to +11.0" in held["freehit"] and "GW11" not in held["freehit"]
    small = {"freehit": "best gain +3.0 below the 8-point threshold"}
    sc.max_gain = 5.0  # its proven bound cannot reach the threshold: the hold is proven
    chips_mod._unproven(small, [sc], {"freehit": (10, 19)}, 17, chips_mod.ChipConfig())
    assert "not proven" not in small["freehit"]


def test_solver_reports_a_time_limited_solve_as_unproven():
    from fpl_agent.solver.mip import SolveInfo

    info = SolveInfo(False, 1, "Time limit reached", gap=0.02, bound=101.0)
    assert info.describe().startswith("time limit, not proven optimal")
    assert "bound 101.00" in info.describe() and "gap 2.0%" in info.describe()
    assert SolveInfo(True, 0, "ok").describe() == "optimal"


def test_adaptive_scoring_rescores_borderline_counts_in_every_world(world, monkeypatch):
    proj, st = world
    n_all = proj.points.shape[2]

    def worlds(k, n):  # one move sits right on the 55% line; two moves clearly lose
        if k == 0:
            return np.zeros(n)
        if k == 1:
            return np.where(np.arange(n) % 100 < 55, 1.2, -0.3)
        return np.full(n, -3.0)

    monkeypatch.setattr(chips_mod, "score_plan", scored(worlds))
    fixed = compare.compare_counts(_base(proj, st), proj, RULES, CFG, 0.55, 1.0, 40)
    adapt = compare.compare_counts(_base(proj, st), proj, RULES, CFG, 0.55, 1.0, 40, adaptive=True)
    one_f = next(o for o in fixed.options if o.transfers == 1)
    one_a = next(o for o in adapt.options if o.transfers == 1)
    assert one_f.worlds == 40 and one_a.worlds == n_all > 40
    two = next(o for o in adapt.options if o.transfers == 2)
    assert two.worlds == 40  # far from every threshold: not rescored
    r = next(x for x in adapt.rows() if x["transfers"] == 1)
    assert r["gain_q10"] <= r["gain"] <= r["gain_q90"] and r["gain_sd"] > 0
    assert len(r["xi"]) == 11 and r["captain"] in r["xi"]
