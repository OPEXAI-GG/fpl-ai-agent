from fpl_agent.data.entry import SquadState
from fpl_agent.execution.live_state import overlay
from fpl_agent.rules import Rules
from fpl_agent.solver.planner import SquadPlayer

RULES = Rules.nearest(2026)
SHAPE = [1] * 2 + [2] * 5 + [3] * 5 + [4] * 3


def raw():
    return {"elements": [
        {"id": i, "element_type": SHAPE[(i - 1) % 15], "team": i, "now_cost": 50}
        for i in range(1, 31)
    ]}  # fmt: skip


def base():
    squad = [SquadPlayer(i, "GKP", i, 50, 50) for i in range(1, 16)]
    return SquadState(next_gw=6, squad=squad, bank=8, free_transfers=1)


def my(ids, **extra):
    picks = [{"element": i, "purchase_price": 50, "selling_price": 52} for i in ids]
    return {"picks": picks, "transfers": {"bank": 3, "value": 1000, "limit": 2, "made": 0},
            "chips": [], **extra}  # fmt: skip


def test_the_current_team_replaces_the_last_deadlines():
    s, changed, note = overlay(base(), my(range(1, 16)), raw(), RULES)
    assert not changed and "matches" in note
    assert s.bank == 3 and s.free_transfers == 2 and s.squad[0].sell_price == 52


def test_a_team_changed_since_the_deadline_blocks_more_transfers_and_chips():
    s, changed, note = overlay(base(), my(range(16, 31)), raw(), RULES)
    assert changed and s.already_transferred == 15 and "differs" in note
    assert {p.player_id for p in s.squad} == set(range(16, 31))


def test_chips_already_played_or_active_are_counted():
    chips = [{"name": "wildcard", "status_for_entry": "active", "played_by_entry": []},
             {"name": "bboost", "status_for_entry": "available"}]  # fmt: skip
    s, _, _ = overlay(base(), my(range(1, 16), chips=chips), raw(), RULES)
    assert ("wildcard", 6) in s.chips_used and not any(n == "bboost" for n, _ in s.chips_used)


def test_an_unreadable_reply_keeps_the_old_squad():
    b = base()
    s, changed, note = overlay(b, {"picks": []}, raw(), RULES)
    assert s is b and not changed and "unreadable" in note
