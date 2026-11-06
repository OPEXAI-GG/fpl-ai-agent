"""The planner's XI follows the execution rails' availability rule, and a manager's completed
moves can be followed by a dry comparison of further ones."""

from dataclasses import replace

import pytest

from fpl_agent.execution.safety import _bad_availability
from fpl_agent.solver import compare
from fpl_agent.solver.decide import decide
from fpl_agent.solver.inputs import build_candidates, prune
from fpl_agent.solver.planner import PlanInput, SquadPlayer
from tests.test_decide import CFG, RULES, projection, state_for


@pytest.fixture(scope="module")
def world():
    proj, cands = projection(3)
    return proj, cands, state_for(cands, bank=50, ft=1)


def _doubtful(proj, ids, status="d", chance=75.0):
    meta = proj.meta.copy()
    meta.loc[meta.player_id.isin(list(ids)), ["status", "chance"]] = [status, chance]
    return replace(proj, meta=meta)


def _made_one_move(cands, st):
    """The manager swapped the squad's cheapest midfielder for an affordable one, by hand."""
    have = {p.player_id for p in st.squad}
    out = min((p for p in st.squad if p.position == "MID"), key=lambda p: p.sell_price)
    clubs = [p.team_id for p in st.squad if p.player_id != out.player_id]
    inn = next(c for c in sorted(cands, key=lambda c: -sum(c.xp))
               if c.position == "MID" and c.player_id not in have
               and c.price <= out.sell_price + st.bank and clubs.count(c.team_id) < 3)  # fmt: skip
    squad = [p for p in st.squad if p.player_id != out.player_id]
    squad.append(SquadPlayer(inn.player_id, "MID", inn.team_id, inn.price, inn.price))
    bank = st.bank + out.sell_price - inn.price
    return out.player_id, inn.player_id, replace(st, squad=squad, bank=bank, base_squad=st.squad)


def test_a_doubtful_starter_gives_a_lineup_the_rails_accept(world):
    proj, _, st = world
    locked = replace(st, already_transferred=1)  # squad fixed: only the lineup can change
    first = decide(locked, proj, RULES, cfg=CFG, use_chips=False)
    star = first.captain
    doubtful = _doubtful(proj, [star])
    d = decide(locked, doubtful, RULES, cfg=CFG, use_chips=False)
    assert star not in d.xi and star in d.bench and d.captain != star
    assert len(d.xi) == 11 and _bad_availability(doubtful.meta, d.xi) == []
    assert any("not started under the availability rule" in w for w in d.warnings)
    assert not any("execution would block it" in w for w in d.warnings)


def test_when_no_legal_xi_avoids_them_the_report_says_execution_would_block_it(world):
    proj, _, st = world
    locked = replace(st, already_transferred=1)
    gks = [p.player_id for p in st.squad if p.position == "GKP"]
    d = decide(locked, _doubtful(proj, gks, "i", 0.0), RULES, cfg=CFG, use_chips=False)
    assert len(d.xi) == 11  # a lineup is still produced, and flagged
    assert any("execution would block it" in w for w in d.warnings)


def test_confirmed_manual_transfers_with_one_free_transfer_left_compare_further_moves(world):
    proj, cands, st = world
    sold, bought, made = _made_one_move(cands, st)
    dry = replace(made, already_transferred=1, free_transfers=2, remaining_free_transfers=1)
    d = decide(dry, proj, RULES, cfg=CFG, use_chips=False)
    rows = {r["transfers"]: r for r in d.comparison}
    assert sorted(rows) == [0, 1, 1 + CFG.max_hits_per_gw]  # 1 free (not 2 - 1 again), + hits
    assert rows[1]["hits"] == 0 and rows[2]["hits"] == 1
    for r in rows.values():
        assert bought not in r["out"] and sold not in r["in"]  # the completed move stands
    assert bought in d.week.squad and sold not in d.transfers_in
    # the live path never sets the remaining count: a hand-edited team stays locked there
    live = decide(replace(dry, remaining_free_transfers=None), proj, RULES, cfg=CFG,
                  use_chips=False)  # fmt: skip
    assert live.comparison == [] and live.transfers_in == []
    assert "squad locked" in live.comparison_note


def test_zero_free_transfers_left_charges_a_hit_for_any_further_move(world):
    proj, cands, st = world
    _, _, made = _made_one_move(cands, st)
    dry = replace(made, already_transferred=1, free_transfers=1, remaining_free_transfers=0)
    d = decide(dry, proj, RULES, cfg=CFG, use_chips=False)
    rows = {r["transfers"]: r for r in d.comparison}
    assert sorted(rows) == list(range(0, CFG.max_hits_per_gw + 1))
    assert rows[0]["hits"] == 0 and rows[1]["hits"] == 1
    assert d.hits == (1 if d.transfers_in else 0)
    assert d.ft_after == 1  # 0 left, + 1 next week either way: never negative, never banked
    # the hit is charged in the points too: this week's xP is the XI's points less 4 per hit
    base = PlanInput(gws=list(proj.gws), squad=dry.squad, bank=dry.bank, free_transfers=0,
                     candidates=prune(build_candidates(proj, dry.squad), dry.squad))  # fmt: skip
    one = compare.compare_counts(base, proj, RULES, CFG, 0.55, 1.0, 60).options[1]
    assert one.plan.first.hits == 1 and one.hits == 1
    assert one.week_xp == pytest.approx(one.plan.first.xp - 4)
