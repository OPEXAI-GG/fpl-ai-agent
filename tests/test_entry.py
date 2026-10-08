"""Squad state from FPL's public entry endpoints: selling prices, free transfers, chips."""

import pytest

from fpl_agent.data.entry import free_transfers_at, parse_squad
from fpl_agent.rules import Rules

RULES = Rules.load(2026)


def _bootstrap():
    """Twenty clubs, 25 players each (3 GKP, 8 DEF, 10 MID, 4 FWD is not needed: 2/5/5/3 per
    squad, so a pool of 15 per club is plenty)."""
    els, eid = [], 0
    for team in range(1, 9):
        for typ, n in ((1, 2), (2, 5), (3, 5), (4, 3)):
            for _ in range(n):
                eid += 1
                els.append(
                    {
                        "id": eid,
                        "team": team,
                        "element_type": typ,
                        "now_cost": 45 + eid % 40,
                        "cost_change_start": eid % 3,
                    }
                )
    return {"elements": els}


BOOT = _bootstrap()


def legal_squad():
    need, per_club, out = {1: 2, 2: 5, 3: 5, 4: 3}, {}, []
    for e in BOOT["elements"]:
        t = e["element_type"]
        if need[t] and per_club.get(e["team"], 0) < 2:
            out.append(e)
            need[t] -= 1
            per_club[e["team"]] = per_club.get(e["team"], 0) + 1
    return out


def picks_for(els, bank=15, value=1000):
    return {
        "picks": [
            {
                "element": e["id"],
                "position": i + 1,
                "multiplier": 1,
                "is_captain": i == 0,
                "is_vice_captain": i == 1,
            }
            for i, e in enumerate(els)
        ],
        "entry_history": {"bank": bank, "value": value},
    }


def history(chips=(), made=None):
    return {
        "chips": [{"name": n, "event": ev} for n, ev in chips],
        "current": [{"event": g, "event_transfers": n} for g, n in (made or {}).items()],
    }


def test_first_week_has_one_free_transfer():
    assert free_transfers_at(history(), 2) == 1


def test_unused_transfers_bank_up_to_the_cap():
    assert free_transfers_at(history(), 4) == 3
    assert free_transfers_at(history(), 20) == 5


def test_using_a_transfer_resets_the_bank():
    h = history(made={2: 1, 3: 0})
    assert free_transfers_at(h, 4) == 2  # used 1 in gw2 (ft1->0 +1), none in gw3 (+1)


def test_hits_do_not_go_below_one_next_week():
    assert free_transfers_at(history(made={2: 4}), 3) == 1


def test_wildcard_and_free_hit_weeks_do_not_consume_free_transfers():
    h = history(chips=[("wildcard", 3)], made={2: 0, 3: 9})
    assert free_transfers_at(h, 4) == 3


def test_selling_price_is_half_the_profit_rounded_down():
    els = legal_squad()
    e = els[3]
    transfers = [
        {
            "element_in": e["id"],
            "element_in_cost": e["now_cost"] - 5,
            "event": 2,
            "time": "2026-08-20T10:00:00Z",
        }
    ]
    st = parse_squad(picks_for(els), transfers, history(), BOOT, 6, RULES)
    sp = next(s for s in st.squad if s.player_id == e["id"])
    assert sp.buy_price == e["now_cost"] - 5
    assert sp.sell_price == e["now_cost"] - 5 + 2  # half of 5, rounded down


def test_original_squad_uses_the_opening_price():
    els = legal_squad()
    st = parse_squad(picks_for(els), [], history(), BOOT, 6, RULES)
    assert len(st.squad) == 15 and st.bank == 15
    for s, e in zip(st.squad, els, strict=True):
        assert s.buy_price == e["now_cost"] - e.get("cost_change_start", 0)


def test_transfers_already_made_for_the_next_week_are_reported_not_applied():
    els = legal_squad()
    t = [
        {
            "element_in": els[0]["id"],
            "element_in_cost": 1,
            "event": 6,
            "time": "2026-09-01T10:00:00Z",
        }
    ]
    st = parse_squad(picks_for(els), t, history(), BOOT, 6, RULES)
    assert st.already_transferred == 1


def test_chips_available_by_window():
    st = parse_squad(picks_for(legal_squad()), [], history(chips=[("3xc", 2)]), BOOT, 6, RULES)
    avail = st.chips_available(RULES)
    assert "3xc" not in avail
    assert (
        avail["wildcard"] == (6, 19) and avail["bboost"] == (6, 19) and avail["freehit"] == (6, 19)
    )


def test_second_half_window_resets_chips():
    st = parse_squad(
        picks_for(legal_squad()), [], history(chips=[("wildcard", 3)]), BOOT, 20, RULES
    )
    assert st.chips_available(RULES)["wildcard"] == (20, 38)


@pytest.mark.parametrize("gw", [1, 39])
def test_no_chips_outside_any_window(gw):
    st = parse_squad(picks_for(legal_squad()), [], history(), BOOT, gw, RULES)
    assert "freehit" not in st.chips_available(RULES)
