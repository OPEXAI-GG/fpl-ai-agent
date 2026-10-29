"""Lessons: a finished gameweek's mistakes, how the next run avoids them, and the memory the
plan reads."""

import json
from datetime import datetime

import pandas as pd
import pytest

from fpl_agent import lessons
from fpl_agent.db.database import FootballDB
from fpl_agent.lessons import Mistake, Week
from fpl_agent.solver.planner import PlannerConfig

# GW7 as it was played: ids are FPL element ids
NAMES = {1: "Raya", 2: "Calafiori", 3: "Truffert", 4: "De Cuyper", 5: "Tarkowski", 6: "Mbeumo",
         7: "Groß", 8: "Barnes", 9: "Palmer", 10: "Haaland", 11: "Evanilson", 12: "Leno",
         13: "Le Fée", 14: "Thomas-Asante", 15: "Davis", 20: "Van Dijk", 21: "Maguire",
         22: "Saka", 23: "Konsa", 24: "João Pedro"}  # fmt: skip
POS = {1: 1, 12: 1, 2: 2, 3: 2, 4: 2, 5: 2, 15: 2, 20: 2, 21: 2, 23: 2, 6: 3, 7: 3, 8: 3, 9: 3,
       13: 3, 22: 3, 10: 4, 11: 4, 14: 4, 24: 4}  # fmt: skip
PTS = {1: 9, 2: 6, 3: 6, 4: 6, 5: 0, 6: 8, 7: 5, 8: 2, 9: 2, 10: 13, 11: 12, 12: 6, 13: 4,
       14: 5, 15: 0, 20: 4, 21: 4, 22: 3, 23: 9, 24: 2}  # fmt: skip
HAND = [{"element_in": 9, "element_out": 22, "time": "2026-10-15T09:20:13Z"},
        {"element_in": 4, "element_out": 21, "time": "2026-10-15T09:19:31Z"},
        {"element_in": 2, "element_out": 20, "time": "2026-10-15T09:19:31Z"}]  # fmt: skip


def week(**kw) -> Week:
    base = dict(
        gw=7, names=NAMES, position=POS, pts=PTS, mins={p: 90 for p in PTS},
        xi=[1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11], bench=[13, 14, 15, 12], captain=10,
        multiplier=2, before={1, 20, 15, 5, 21, 13, 6, 22, 7, 8, 10, 12, 11, 3, 14},
        transfers=HAND, hits=0,
        bot_plan={"transfers_in": [], "xp": 53.7,
                  "plan": [{"gw": 8, "in": [2, 23, 9], "out": [3, 5, 22]}]},
        field={2: 94.0, 4: 83.0, 9: 52.0, 10: 98.0, 24: 98.0}, official=82, expected=53.7,
    )  # fmt: skip
    return Week(**{**base, **kw})


def kinds(ms: list[Mistake]) -> dict[str, Mistake]:
    return {m.kind: m for m in ms}


def test_gw7_hand_transfers_bench_and_held_moves():
    ms = kinds(lessons.find_mistakes(week()))
    hand = ms["hand_transfers"]
    assert hand.rule == "act_now" and hand.points == 3
    assert "Saka 3 -> Palmer 2" in hand.what and "the bot's plan was to roll" in hand.what
    assert "planned Palmer, Calafiori for a later week" in hand.lesson
    assert "De Cuyper 83%" in hand.lesson
    # the held Tarkowski -> Konsa: Tarkowski started on 0, Konsa scored 9
    held = ms["deferred_moves"]
    assert held.rule == "act_now" and held.points == 9 and "Tarkowski 0 -> Konsa 9" in held.what
    # Saka was already sold by hand, Truffert's swap gained nothing: neither counts
    assert "Saka" not in held.what and "Truffert" not in held.what
    bench = ms["bench"]
    assert bench.points == 5 and "Tarkowski 0 started while Thomas-Asante 5" in bench.what
    assert bench.rule is None
    assert {"captain", "goalkeeper", "hits", "missed_template_haul"}.isdisjoint(ms)


def test_hand_moves_that_lost_change_nothing():
    pts = {**PTS, 9: 0, 22: 15}
    m = kinds(lessons.find_mistakes(week(pts=pts)))["hand_transfers"]
    assert m.points < 0 and m.rule is None and "hold was right" in m.lesson


def test_break_even_hand_moves_change_nothing():
    """C03: a zero-gain manual move is no evidence and must not lower the bar for moves."""
    pts = {**PTS, 9: 3, 4: 4, 2: 4}  # each hand move scores what the player it replaced did
    m = kinds(lessons.find_mistakes(week(pts=pts)))["hand_transfers"]
    assert m.points == 0 and m.rule is None and "broke even" in m.lesson


def test_hand_moves_that_broke_even_change_nothing(db):
    """Audit case: a zero-point gain must not switch on act_now (and so must not lower the
    bar for transfers through the memory)."""
    pts = {**PTS, 22: 6}  # 2 + 6 + 6 in against 6 + 4 + 4 out
    m = kinds(lessons.find_mistakes(week(pts=pts)))["hand_transfers"]
    assert m.points == 0 and m.rule is None and "broke even" in m.lesson
    lessons.save(db, 2026, 7, [m])
    assert lessons.memory(db, 2026, 8).w("act_now") == 0.0


def test_moves_the_bot_made_are_not_hand_moves():
    ms = kinds(lessons.find_mistakes(week(bot_ins={2, 4, 9})))
    assert "hand_transfers" not in ms


def test_captain_goalkeeper_hits_and_missed_hauls():
    pts = {**PTS, 10: 2, 12: 14, 24: 15}
    w = week(pts=pts, hits=1, bot_ins={2, 4, 9}, transfers=HAND[:2],
             bot_plan={"transfers_in": [9, 4], "plan": [],
                       "captain_options": [{"player_id": 11, "xpts": 4.5}]})  # fmt: skip
    ms = kinds(lessons.find_mistakes(w))
    assert ms["captain"].points == 10 and "on the captain shortlist" in ms["captain"].lesson
    assert ms["goalkeeper"].points == 5
    assert ms["hits"].rule == "hits" and ms["hits"].points == 3  # moves +1, hit -4
    assert (
        ms["missed_template_haul"].points == 15
        and "João Pedro 15" in ms["missed_template_haul"].what
    )
    # a captain off the shortlist means the projection was too low
    w.bot_plan["captain_options"] = []
    assert "not on the captain shortlist" in kinds(lessons.find_mistakes(w))["captain"].lesson


def test_bench_swaps_must_keep_a_legal_formation():
    # three defenders start: the defender cannot leave for a forward
    xi = [1, 2, 3, 5, 6, 7, 8, 9, 10, 11, 13]
    w = week(xi=xi, bench=[14, 4, 15, 12], pts={**PTS, 4: 0, 5: 0, 14: 8, 13: 4, 8: 4, 9: 4})
    m = kinds(lessons.find_mistakes(w))["bench"]
    assert "Tarkowski" not in m.what and "Thomas-Asante 8" in m.what


@pytest.fixture
def db(tmp_path):
    return FootballDB(tmp_path / "t.duckdb")


def test_memory_decays_renews_and_changes_the_plan(db):
    lessons.save(db, 2026, 7, [Mistake("hand_transfers", 3, "w", "make moves now", "act_now"),
                               Mistake("bench", 5, "b", "l")])  # fmt: skip
    mem = lessons.memory(db, 2026, 8)
    assert mem.w("act_now") == 1.0 and mem.min_win(0.55) == pytest.approx(0.50)
    cfg = mem.planner(PlannerConfig())
    assert cfg.ft_values[0] == pytest.approx(2.0 * 0.7) and cfg.hit_buffer == 2.0
    assert any("act now" in n for n in mem.notes())
    assert any("make moves now" in n for n in mem.notes())
    # fades: four weeks later 0.6^4 is below the active weight
    assert lessons.memory(db, 2026, 12).w("act_now") == 0.0
    assert lessons.memory(db, 2026, 12).planner(PlannerConfig()) == PlannerConfig()
    # re-running a gameweek replaces its lessons
    lessons.save(db, 2026, 7, [Mistake("hits", 3, "w", "l", "hits")])
    mem = lessons.memory(db, 2026, 8)
    assert mem.w("act_now") == 0.0 and mem.planner(PlannerConfig()).hit_buffer == 3.0


def test_a_watched_mistake_three_times_in_six_weeks_is_repeated(db):
    for gw in (4, 6, 7):
        lessons.save(db, 2026, gw, [Mistake("goalkeeper", 4, "w", "l")])
    mem = lessons.memory(db, 2026, 8)
    assert mem.repeated == {"goalkeeper": 3}
    assert any("repeated mistake, goalkeeper in 3" in n for n in mem.notes())
    assert lessons.memory(db, 2026, 12).repeated == {}  # GW4 and GW6 are out of the window


class FakeProj:
    def __init__(self):
        import numpy as np

        self.player_ids = [100, 200]
        self.points = np.ones((2, 1, 3), dtype=np.float32)


def test_template_correction_is_learned_from_the_reviews(db):
    for gw, pred, act in ((5, 4.0, 5.0), (6, 4.0, 5.0)):
        payload = {"template": {"n": 75, "mean_pred": pred, "mean_actual": act}}
        db.append("reviews", pd.DataFrame([{"season": 2026, "gw": gw, "kind": "prediction",
                                            "created_at": datetime(2026, 10, 1),
                                            "payload": json.dumps(payload)}]))  # fmt: skip
    f, n = lessons.template_factor(db, 2026, 8)
    assert n == 150 and f == pytest.approx(1.10)  # 1 + 0.5 * 0.25, capped at +10%
    mem = lessons.memory(db, 2026, 8)
    proj = FakeProj()
    assert mem.apply_template(proj, {100: 80.0, 200: 10.0}) == [100]
    assert proj.points[0].max() == pytest.approx(1.1) and proj.points[1].max() == 1.0
    assert any("x1.100, learned from 150 player-weeks (1 players)" in x for x in mem.notes())
    assert lessons.template_factor(db, 2026, 5) == (1.0, 0)


class FakeFPL:
    def bootstrap_raw(self):
        return {"events": [{"id": 7, "finished": False}],
                "elements": [{"id": i, "web_name": n, "element_type": POS[i]}
                             for i, n in NAMES.items()]}  # fmt: skip

    def live_raw(self, gw):
        return {"elements": [{"id": i, "stats": {"total_points": p, "minutes": 90}}
                             for i, p in PTS.items()]}  # fmt: skip

    def entry_picks_raw(self, entry, gw):
        if gw == 6:
            return {"picks": [{"element": i} for i in week().before]}
        order = [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 13, 14, 15, 12]
        picks = [{"element": e, "position": n + 1, "multiplier": 1 if n < 11 else 0,
                  "is_captain": e == 10} for n, e in enumerate(order)]  # fmt: skip
        picks[9]["multiplier"] = 2
        return {"picks": picks, "active_chip": None, "automatic_subs": [],
                "entry_history": {"points": 82, "event_transfers_cost": 0}}  # fmt: skip

    def entry_transfers_raw(self, entry):
        return [
            *({**t, "event": 7} for t in HAND),
            {"element_in": 99, "element_out": 98, "event": 6},
        ]


def test_run_reads_fpl_and_the_bots_decisions(db):
    # canonical ids are FPL id + 1000; the bot's plan run came before the hand moves
    db.append("fpl_players_gw", pd.DataFrame([{"season": 2026, "gw": 7, "fpl_id": i,
                                               "player_id": i + 1000,
                                               "snapshot_at": datetime(2026, 10, 15)}
                                              for i in NAMES]))  # fmt: skip
    plan = {
        "gw": 7,
        "transfers_in": [],
        "transfers_out": [],
        "xi": [],
        "bench": [],
        "xp": 53.7,
        "plan": [{"gw": 8, "in": [1002, 1023, 1009], "out": [1003, 1005, 1022]}],
    }
    late = {**plan, "xp": 60.0, "plan": []}
    db.append("decisions", pd.DataFrame([
        {"season": 2026, "gw": 7, "created_at": datetime(2026, 10, 15, 9, 17), "mode": "live",
         "payload": json.dumps(plan)},
        {"season": 2026, "gw": 7, "created_at": datetime(2026, 10, 17, 6, 54), "mode": "live",
         "payload": json.dumps(late)},
    ]))  # fmt: skip
    db.append("projections", pd.DataFrame([{"season": 2026, "gw": 7, "player_id": 1005,
                                            "xpts": 3.1, "created_at": datetime(2026, 10, 17)},
                                           {"season": 2026, "gw": 7, "player_id": 1014,
                                            "xpts": 2.4, "created_at": datetime(2026, 10, 17)}]))  # fmt: skip
    text = lessons.run(db, FakeFPL(), 2026, 7, 1)
    assert text.startswith("GW7 lessons (PROVISIONAL")
    assert "82 points, the plan expected 60.0 before hits" in text
    assert "Tarkowski 0 -> Konsa 9" in text  # the plan from before the hand moves
    assert "Tarkowski 0 (exp 3.1) started while Thomas-Asante 5 (exp 2.4)" in text
    assert "the projection's miss" in text
    stored = db.query("SELECT kind, rule FROM lessons WHERE gw = 7 ORDER BY kind")
    assert list(stored["kind"]) == ["bench", "deferred_moves", "hand_transfers"]
    assert lessons.memory(db, 2026, 8).w("act_now") == 1.0
