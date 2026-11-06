"""The current team from public data, screenshots and corrections: validation, resolution,
evidence merging, staleness, free-transfer scenarios and the planner state."""

from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from fpl_agent.rules import Rules
from fpl_agent.team import current_team, store
from fpl_agent.team.merge import CONFIRMED, CONFLICTING, INFERRED, UNKNOWN, merge, to_state
from fpl_agent.team.observation import Observation, resolve, tenths
from fpl_agent.team.public import public_view

RULES = Rules.load(2026)
GW = 10
NOW = datetime(2026, 11, 5, 14, 0, tzinfo=UTC)
DEADLINE9 = "2026-10-31T11:00:00Z"
SHORT = ["ARS", "BOU", "BHA", "EVE", "IPS", "MCI", "MUN", "NEW", "HUL", "SUN"]


def _bootstrap():
    """Ten clubs with 2 GKP, 5 DEF, 5 MID, 3 FWD each. Two defenders called Davis (IPS and
    BOU) and two midfielders called Barnes (NEW and SUN) exist to test ambiguity."""
    els, eid = [], 0
    for team in range(1, 11):
        for typ, n in ((1, 2), (2, 5), (3, 5), (4, 3)):
            for k in range(n):
                eid += 1
                name = f"P{eid}"
                if typ == 2 and k == 0 and team in (2, 5):
                    name = "Davis"
                if typ == 3 and k == 0 and team in (8, 10):
                    name = "Barnes"
                els.append({"id": eid, "team": team, "element_type": typ, "web_name": name,
                            "first_name": "", "second_name": name, "now_cost": 50 + eid % 30,
                            "cost_change_start": 0})  # fmt: skip
    teams = [{"id": i + 1, "short_name": s, "name": s} for i, s in enumerate(SHORT)]
    events = [{"id": 9, "deadline_time": DEADLINE9}, {"id": 10, "deadline_time": "2026-11-06"}]
    return {"elements": els, "teams": teams, "events": events}


BOOT = _bootstrap()
ELS = {e["id"]: e for e in BOOT["elements"]}
# GW10: HUL (9) v ARS (1) ... a fixture per pair of clubs
FIXTURES = [{"event": GW, "team_h": h, "team_a": a} for h, a in ((9, 1), (5, 2), (3, 4),
                                                              (7, 6), (10, 8))]  # fmt: skip


def _squad():
    need, per, out = {1: 2, 2: 5, 3: 5, 4: 3}, {}, []
    for e in BOOT["elements"]:
        if e["web_name"] in ("Davis", "Barnes"):
            continue
        t = e["element_type"]
        if need[t] and per.get(e["team"], 0) < 2:
            out.append(e["id"])
            need[t] -= 1
            per[e["team"]] = per.get(e["team"], 0) + 1
    return out


SQUAD = _squad()
SPARE = next(e["id"] for e in BOOT["elements"] if e["element_type"] == 4 and e["id"] not in SQUAD
             and sum(ELS[s]["team"] == e["team"] for s in SQUAD) < 3)  # fmt: skip


def raw_public(transfers=(), squad=SQUAD, bank=5, history=None):
    picks = {
        "picks": [{"element": e, "position": i + 1, "is_captain": i == 14,
                   "is_vice_captain": i == 13} for i, e in enumerate(squad)],
        "entry_history": {"bank": bank, "value": 1000},
    }  # fmt: skip
    made = {7: 3, 9: 2}  # replayed: 3 free transfers at the start of GW10
    hist = history or {"current": [{"event": g, "event_transfers": made.get(g, 0)}
                                   for g in range(1, 10)],
                       "chips": [{"name": "wildcard", "event": 6}]}  # fmt: skip
    return public_view(1, GW, picks, list(transfers), hist, BOOT, RULES, NOW)


def card(e, **kw):
    pos = {1: "GKP", 2: "DEF", 3: "MID", 4: "FWD"}[ELS[e]["element_type"]]
    return {"name": ELS[e]["web_name"], "position": pos, "club": SHORT[ELS[e]["team"] - 1], **kw}


def obs(players=None, at=NOW - timedelta(minutes=30), **kw):
    data = {"kind": "screenshot", "screen": "transfers", "entry": 1, "gameweek": GW,
            "observed_at": at.isoformat(), "players": players or [], **kw}  # fmt: skip
    return Observation.model_validate(data)


def merged(observations, public=None, now=NOW):
    pub = raw_public() if public is None else public
    return merge(pub or None, [resolve(o, BOOT, FIXTURES) for o in observations], entry=1,
                 season=2026, gameweek=GW, now=now, rules=RULES)  # fmt: skip


def transfer(out, inn, minutes_ago, cost=None):
    t = NOW - timedelta(minutes=minutes_ago)
    return {"event": GW, "element_out": out, "element_in": inn,
            "element_in_cost": cost or ELS[inn]["now_cost"],
            "time": t.isoformat().replace("+00:00", "Z")}  # fmt: skip


def pick_team(squad, at=NOW - timedelta(minutes=10)):
    cards = [card(e, slot="xi" if i < 11 else "bench", bench_order=i - 11 if i >= 11 else None,
                  captain=i == 0, vice=i == 1) for i, e in enumerate(squad)]  # fmt: skip
    return obs(cards, at=at, screen="pick_team")


# --- schema and resolution ---------------------------------------------------------------


def test_money_is_integer_tenths_and_round_trips():
    assert tenths("£0.2m") == 2 and tenths(0.2) == 2 and tenths("6.1") == 61 and tenths(61) == 61
    o = obs([card(e, price="£5.5m") for e in SQUAD], bank="£0.2m", free_transfers=3)
    again = Observation.model_validate_json(o.model_dump_json())
    assert again.bank == 2 and again.players[0].price == 55 and again == o


def test_invalid_input_is_rejected_with_a_reason():
    with pytest.raises(ValidationError):
        obs(free_transfers=7)
    with pytest.raises(ValidationError):
        obs(screen="pick_team", pending=True)
    with pytest.raises(ValidationError):
        obs(chips={"turbo": "available"})


def test_players_resolve_by_name_position_club_and_fixture():
    davis_ips = next(e["id"] for e in BOOT["elements"] if e["web_name"] == "Davis"
                     and e["team"] == 5)  # fmt: skip
    r = resolve(obs([{"name": "davis", "position": "DEF", "opponent": "BOU (H)"},
                     {"name": "P1", "position": "GKP"}]), BOOT, FIXTURES)  # fmt: skip
    assert r.elements == [davis_ips, 1] and not r.problems


def test_ambiguous_names_stay_unresolved():
    r = resolve(obs([{"name": "Davis", "position": "DEF"}, {"name": "Nobody"}]), BOOT, FIXTURES)
    assert r.elements == [None, None]
    assert "ambiguous" in r.problems[0] and "BOU" in r.problems[0] and "IPS" in r.problems[0]
    assert "no player matches" in r.problems[1]


def test_a_shown_price_above_the_current_price_is_not_a_selling_price():
    cards = [card(e) for e in SQUAD]
    cards[0]["price"] = ELS[SQUAD[0]]["now_cost"] + 5
    assert any("not a selling price" in e for e in resolve(obs(cards), BOOT, FIXTURES).errors)


# --- merging -------------------------------------------------------------------------------


def test_public_only_is_inferred_and_the_free_transfer_count_is_an_estimate():
    t = merged([])
    assert t.squad.status == INFERRED and t.squad.value == sorted(SQUAD)
    assert t.free_transfers.status == INFERRED and "does not publish" in t.free_transfers.note
    assert t.free_transfers.value == 3
    assert t.ft_scenarios() == [3, 1, 0]  # this week's transfers are not public yet
    assert "only after the deadline" in t.transfers_made.note
    assert t.bank.value == 5 and t.bank.status == INFERRED
    assert t.chips["wildcard"].value == "used GW6" and t.chips["freehit"].status == INFERRED
    q = " ".join(t.questions(True))
    assert "free-transfer count" in q and "entry" not in q and "15" not in q
    assert "if you made transfers this gameweek" in q


def test_confirmed_screenshot_beats_public_last_deadline_picks():
    current = [*SQUAD[:-1], SPARE]  # the manager changed a forward after the last deadline
    t = merged([pick_team(current)])
    assert t.squad.status == CONFIRMED and t.squad.value == sorted(current)
    assert "public data still shows" in t.squad.note
    assert t.transfers_made.value == 1 and t.transfers_made.status == CONFIRMED
    assert t.lineup.status == CONFIRMED and t.lineup.value[2] == current[0]
    st = to_state(t, BOOT, RULES)
    assert st.already_transferred == 1 and {p.player_id for p in st.squad} == set(current)
    assert st.free_transfers == t.free_transfers.value + 1  # the planner subtracts it again


def test_public_transfer_after_a_screenshot_supersedes_it():
    pub = raw_public([transfer(SQUAD[-1], SPARE, minutes_ago=5)])
    t = merged([pick_team(SQUAD, at=NOW - timedelta(minutes=60))], public=pub)
    assert t.squad.status == INFERRED and SPARE in t.squad.value
    assert "after the pick_team screenshot" in t.squad.note


def test_unconfirmed_transfer_preview_never_replaces_the_squad():
    basket = [card(e, price=ELS[e]["now_cost"]) for e in [*SQUAD[:-1], SPARE]]
    for kw in ({"pending": True}, {"pending": None}, {"transfer_cost": 4}):
        t = merged([obs(basket, free_transfers=2, bank=0, **kw)])
        assert t.squad.value == sorted(SQUAD) and t.squad.status == INFERRED
        assert t.free_transfers.status == INFERRED and t.bank.value == 5
        assert t.previews and "not used as your squad" in t.previews[0]


def test_transfers_screen_matching_the_public_squad_confirms_count_bank_and_prices():
    cards = [card(e, price=ELS[e]["now_cost"] - 1) for e in SQUAD]
    t = merged([obs(cards, free_transfers=2, bank="£0.4m", transfer_cost=0)])
    assert t.squad.status == CONFIRMED and t.free_transfers.value == 2
    assert t.free_transfers.status == CONFIRMED and t.ft_scenarios() == [2]
    assert t.bank.value == 4 and t.bank.status == CONFIRMED
    assert all(f.status == CONFIRMED for f in t.selling.values())
    assert "reconstruction said 3" in t.free_transfers.note


def test_missing_public_data_leaves_fields_unknown_and_asks_only_for_them():
    t = merged([], public=False)
    assert t.squad.status == UNKNOWN and not t.usable
    assert t.free_transfers.status == UNKNOWN and t.bank.status == UNKNOWN
    assert t.ft_scenarios() == [1, 0, 2]
    q = t.questions(True)
    assert any("Pick Team" in x for x in q) and any("Budget" in x for x in q)


def test_ambiguous_screenshot_players_do_not_confirm_the_squad():
    cards = [card(e) for e in SQUAD]
    cards[3] = {"name": "Davis", "position": "DEF"}  # cannot tell which Davis
    t = merged([obs(cards, screen="pick_team")])
    assert t.squad.status == INFERRED and t.unresolved
    assert any("unresolved" in q for q in t.questions(False))


def test_corrections_are_confirmed_and_later_public_transfers_adjust_them():
    fix = Observation.model_validate({"kind": "correction", "entry": 1, "gameweek": GW,
                                      "observed_at": (NOW - timedelta(hours=2)).isoformat(),
                                      "free_transfers": 2, "bank": 7})  # fmt: skip
    t = merged([fix])
    assert t.free_transfers.value == 2 and t.free_transfers.status == CONFIRMED
    assert t.bank.value == 7 and t.ft_scenarios() == [2]
    pub = raw_public([transfer(SQUAD[-1], SPARE, minutes_ago=30)])
    t2 = merged([fix], public=pub)
    assert t2.free_transfers.value == 1 and t2.free_transfers.status == INFERRED
    sold = pub.sold_for[SQUAD[-1]]
    assert t2.bank.value == 7 + sold - ELS[SPARE]["now_cost"]
    assert 0 in t2.ft_scenarios()  # this week's transfers may have used the bank


def test_two_corrections_at_the_same_time_that_disagree_conflict():
    at = (NOW - timedelta(minutes=5)).isoformat()
    a, b = ({"kind": "correction", "entry": 1, "gameweek": GW, "observed_at": at,
             "free_transfers": v} for v in (1, 2))  # fmt: skip
    t = merged([Observation.model_validate(a), Observation.model_validate(b)])
    assert t.free_transfers.status == CONFLICTING and set(t.ft_scenarios()) >= {1, 2, 0}


def test_stale_saved_state_is_not_used_and_shows_its_age(tmp_path):
    old = obs([card(e) for e in SQUAD], gameweek=GW - 1, free_transfers=5,
              at=NOW - timedelta(days=6), pending=False)  # fmt: skip
    store.save(tmp_path, old)
    t = current_team(entry=1, season=2026, gameweek=GW, now=NOW, rules=RULES, bootstrap=BOOT,
                     public=raw_public(), root=tmp_path, fixtures=lambda: FIXTURES)  # fmt: skip
    assert t.free_transfers.status == INFERRED and t.free_transfers.value == 3
    assert t.stale and "6 days ago" in t.stale[0] and "GW9" in t.stale[0]
    from fpl_agent.team.report import team_lines

    text = "\n".join(team_lines(t))
    assert "information cutoff" in text and "days ago" in text and "not used" in text


def test_store_is_idempotent_and_skips_invalid_files(tmp_path):
    o = obs([card(e) for e in SQUAD], free_transfers=1)
    p1, p2 = store.save(tmp_path, o), store.save(tmp_path, o)
    assert p1 == p2 and len(list(p1.parent.glob("*.json"))) == 1
    (p1.parent / "broken.json").write_text("{")
    got, bad = store.load(tmp_path, 1)
    assert got == [o] and bad and "broken.json" in bad[0]


def test_merging_ignores_the_order_of_observations():
    cards = [card(e, price=ELS[e]["now_cost"]) for e in SQUAD]
    many = [obs(cards, free_transfers=2, at=NOW - timedelta(minutes=50)),
            pick_team(SQUAD, at=NOW - timedelta(minutes=20)),
            obs([card(e) for e in [*SQUAD[:-1], SPARE]], pending=True),
            Observation.model_validate({"kind": "correction", "entry": 1, "gameweek": GW,
                                        "observed_at": NOW.isoformat(), "bank": 9})]  # fmt: skip
    a, b = merged(many).as_dict(), merged(list(reversed(many))).as_dict()
    assert a == b and a["bank"]["value"] == 9 and a["free_transfers"]["value"] == 2


def test_confirmed_manual_transfers_with_one_free_transfer_left_are_used_as_is():
    current = [*SQUAD[:-1], SPARE]  # one move already made and confirmed on the Transfers screen
    t = merged([obs([card(e) for e in current], free_transfers=1, bank="£1.9m", pending=False,
                    transfer_cost=0)])  # fmt: skip
    assert t.squad.status == CONFIRMED and t.transfers_made.value == 1
    assert t.free_transfers.value == 1 and t.free_transfers.status == CONFIRMED
    assert t.ft_scenarios() == [1] and t.bank.value == 19
    st = to_state(t, BOOT, RULES)
    assert st.remaining_free_transfers == 1  # used directly, not reduced by the completed move
    assert st.already_transferred == 1 and {p.player_id for p in st.base_squad} == set(SQUAD)


def test_unknown_current_squad_despite_an_empty_public_transfer_list():
    t = merged([])  # FPL lists no transfers before the deadline: that is not "none made"
    assert t.squad.status == INFERRED and t.transfers_made.status == INFERRED
    assert "only after the deadline" in t.transfers_made.note
    assert 0 in t.ft_scenarios()
    assert any("if you made transfers this gameweek" in q for q in t.questions(True))
    st = to_state(t, BOOT, RULES)
    assert st.remaining_free_transfers is None and st.already_transferred == 0
