"""Transfer timing: hold the moves for team news until the last few hours, unless a planned
player is about to change price the wrong way before the deadline."""

from datetime import UTC, datetime

from fpl_agent.solver import timing


def el(i, own, tin, tout, change=0):
    return {"id": i, "selected_by_percent": str(own), "transfers_in_event": tin,
            "transfers_out_event": tout, "cost_change_event": change}  # fmt: skip


def test_price_pressure_reads_net_transfers_against_owners():
    raw = {"total_players": 10_000_000, "elements": [
        el(1, 10.0, 80_000, 10_000),   # +70k vs 50k needed: rise
        el(2, 10.0, 40_000, 10_000),   # +30k: not yet
        el(3, 20.0, 5_000, 150_000),   # -145k vs 100k: fall
        el(4, 10.0, 80_000, 10_000, change=1),  # already rose once: count reset
        el(5, 0.1, 15_000, 0),         # under the floor of net transfers
        el(6, 0.0, 50_000, 0),         # nobody owns him (bad data)
    ]}  # fmt: skip
    assert timing.price_pressure(raw) == {1: 1, 3: -1}
    assert timing.price_pressure({}) == {}


def test_update_before_the_deadline():
    fri = datetime(2026, 10, 16, 10, 0, tzinfo=UTC)
    sat = datetime(2026, 10, 17, 10, 0, tzinfo=UTC)
    assert timing.update_before(sat, fri)  # the 01:30 update on Saturday comes first
    assert not timing.update_before(sat, datetime(2026, 10, 17, 2, 0, tzinfo=UTC))
    assert timing.update_before(sat, datetime(2026, 10, 17, 1, 0, tzinfo=UTC))


def test_hold_moves_for_team_news_unless_prices_move_or_time_is_short():
    hold, why = timing.hold_moves([7], [8], {}, 20.0, True)
    assert hold and "team news" in why
    assert timing.hold_moves([7], [8], {}, 4.0, True) == (False, "")  # the last call
    assert timing.hold_moves([], [], {}, 20.0, True) == (False, "")  # nothing to make
    names = {7: "Saka", 8: "Palmer"}.get
    hold, why = timing.hold_moves([7], [8], {7: 1}, 20.0, True, names)
    assert not hold and "Saka likely to rise" in why
    hold, why = timing.hold_moves([7], [8], {8: -1}, 20.0, True, names)
    assert not hold and "Palmer likely to fall" in why
    assert timing.hold_moves([7], [8], {7: -1, 8: 1}, 20.0, True)[0]  # the right way: wait
    assert timing.hold_moves([7], [8], {7: 1}, 20.0, False)[0]  # no update before the deadline
