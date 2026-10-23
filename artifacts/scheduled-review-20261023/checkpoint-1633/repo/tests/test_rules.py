import collections
import json
from pathlib import Path

import pytest

from fpl_agent.rules import Rules, auto_sub, bonus, effective_captain, score, selling_price

FIX = Path(__file__).parent / "fixtures"
R = Rules.load(2026)
GWS = json.loads((FIX / "rules_gws.json").read_text(encoding="utf-8"))


def test_score_matches_official_points_for_every_player():
    """Acceptance test: recompute FPL's own total_points from the raw stats, exactly."""
    n = 0
    for gw, rows in GWS.items():
        for r in rows:
            got = score(r["stats"], r["pos"], R)["total"]
            assert got == r["stats"]["total_points"], (gw, r)
            n += 1
    assert n > 600


def test_defcon_actually_exercised():
    awarded = [
        r
        for rows in GWS.values()
        for r in rows
        if score(r["stats"], r["pos"], R).get("defensive_contribution")
    ]
    assert len(awarded) > 20  # the fixture covers DefCon scoring, not just goals
    assert not any(r["pos"] == "GKP" for r in awarded)


def test_bonus_matches_official_bonus_for_every_match():
    matches = 0
    for rows in GWS.values():
        by = collections.defaultdict(dict)
        real = {}
        for r in rows:
            if r["fixture"] is not None and r["stats"]["minutes"] > 0:
                by[r["fixture"]][r["id"]] = r["stats"]["bps"]
                real[(r["fixture"], r["id"])] = r["stats"]["bonus"]
        for fx, bps in by.items():
            want = {pid: real[(fx, pid)] for pid in bps if real[(fx, pid)] > 0}
            assert bonus(bps) == want, fx
            matches += 1
    assert matches >= 15


def test_bonus_tie_rules():
    assert bonus({"a": 40, "b": 30, "c": 20, "d": 10}) == {"a": 3, "b": 2, "c": 1}
    assert bonus({"a": 40, "b": 40, "c": 20, "d": 10}) == {"a": 3, "b": 3, "c": 1}
    assert bonus({"a": 40, "b": 30, "c": 30, "d": 10}) == {"a": 3, "b": 2, "c": 2}
    assert bonus({"a": 40, "b": 30, "c": 20, "d": 20}) == {"a": 3, "b": 2, "c": 1, "d": 1}
    assert bonus({"a": 40, "b": 40, "c": 40, "d": 10}) == {"a": 3, "b": 3, "c": 3}


def test_score_thresholds():
    s = lambda **k: score({"minutes": 90, **k}, "DEF", R)  # noqa: E731
    assert s(goals_conceded=1)["total"] == 2  # 2 mins, no deduction for one goal
    assert s(goals_conceded=2)["total"] == 1
    assert s(defensive_contribution=9)["total"] == 2
    assert s(defensive_contribution=10)["total"] == 4
    # clean sheet needs 60 minutes
    assert score({"minutes": 59, "clean_sheets": 1}, "DEF", R)["total"] == 1
    assert score({"minutes": 60, "clean_sheets": 1}, "DEF", R)["total"] == 6
    assert score({"minutes": 90, "saves": 5}, "GKP", R).get("saves") == 1
    assert score({"minutes": 90, "saves": 6}, "GKP", R).get("saves") == 2
    assert score({"minutes": 0, "yellow_cards": 1}, "MID", R)["total"] == 0
    assert score({"minutes": 90, "defensive_contribution": 12}, "MID", R)["total"] == 4
    assert "defensive_contribution" not in score(
        {"minutes": 90, "defensive_contribution": 30}, "GKP", R
    )


POS = {
    "g1": "GKP", "g2": "GKP",
    "d1": "DEF", "d2": "DEF", "d3": "DEF", "d4": "DEF", "d5": "DEF",
    "m1": "MID", "m2": "MID", "m3": "MID", "m4": "MID", "m5": "MID",
    "f1": "FWD", "f2": "FWD", "f3": "FWD",
}  # fmt: skip
XI = ["g1", "d1", "d2", "d3", "d4", "m1", "m2", "m3", "f1", "f2", "f3"]
BENCH = ["g2", "m4", "d5", "m5"]


def test_auto_sub_replaces_zero_minute_starter_in_bench_order():
    mins = {p: 90 for p in POS}
    mins["m2"] = 0
    final = auto_sub(XI, BENCH, mins, POS, R)
    assert "m2" not in final and "m4" in final


def test_one_minute_cameo_prevents_bench_cover():
    mins = {p: 90 for p in POS}
    mins["f1"] = 1
    final = auto_sub(XI, BENCH, mins, POS, R)
    assert final == XI


def test_auto_sub_skips_bench_players_who_did_not_play():
    mins = {p: 90 for p in POS}
    mins.update({"m2": 0, "m4": 0})
    final = auto_sub(XI, BENCH, mins, POS, R)
    assert "d5" in final and "m4" not in final  # d5 (next on bench) can't: 4 DEF, 2 MID, 3 FWD ok


def test_auto_sub_respects_formation_minimums():
    xi = ["g1", "d1", "d2", "d3", "m1", "m2", "m3", "m4", "f1", "f2", "f3"]  # 3-4-3
    mins = {p: 90 for p in POS}
    mins["d1"] = 0
    final = auto_sub(xi, ["g2", "m5", "d4", "d5"], mins, POS, R)
    assert "m5" not in final and "d4" in final  # only a defender keeps 3 DEF


def test_auto_sub_gk_only_swaps_with_gk():
    mins = {p: 90 for p in POS}
    mins["g1"] = 0
    final = auto_sub(XI, ["m4", "g2", "d5", "m5"], mins, POS, R)
    assert "g2" in final and "m4" not in final


def test_captaincy_and_prices():
    assert effective_captain("a", "b", {"a": 90, "b": 90}) == "a"
    assert effective_captain("a", "b", {"a": 0, "b": 45}) == "b"
    assert effective_captain("a", "b", {"a": 0, "b": 0}) is None
    assert selling_price(50, 53) == 51  # half of +0.3 rounds down to +0.1
    assert selling_price(50, 51) == 50
    assert selling_price(50, 48) == 48
    assert selling_price(100, 101) == 100


def test_missing_rules_file():
    with pytest.raises(FileNotFoundError):
        Rules.load(1999)
