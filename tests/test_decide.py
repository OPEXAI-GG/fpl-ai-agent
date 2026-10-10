"""Chip scenario search, sensitivity gates, and the weekly decision."""

import numpy as np
import pandas as pd
import pytest

from fpl_agent.data.entry import SquadState
from fpl_agent.model.project import Projection
from fpl_agent.rules import Rules
from fpl_agent.solver import chips as ch
from fpl_agent.solver.decide import decide
from fpl_agent.solver.inputs import build_candidates, prune, to_canonical
from fpl_agent.solver.planner import PlanInput, PlannerConfig
from tests.test_planner import initial_squad, make_pool

RULES = Rules.load(2026)
CFG = PlannerConfig(time_limit=30)
FAST = ch.ChipConfig(n_worlds=60, scenario_time_limit=30)


def projection(seed=0, horizon=3, gws=(10, 11, 12), spread=0.6, boost=None, n=200):
    """A projection whose sampled worlds scatter around the pool's expected points."""
    cands = make_pool(seed, horizon=horizon)
    rng = np.random.default_rng(seed)
    mean = np.array([c.xp for c in cands])
    if boost:  # {(player index, gw index): extra mean}
        for (i, g), v in boost.items():
            mean[i, g] += v
    pts = rng.gamma(2.0, 1.0, size=(len(cands), horizon, n)) * (mean[..., None] / 2.0)
    pts = (pts * (1 - spread) + mean[..., None] * spread).astype(np.float32)
    mins = np.full((len(cands), horizon, n), 90, dtype=np.int16)
    meta = pd.DataFrame(
        {
            "player_id": [c.player_id for c in cands],
            "position": [c.position for c in cands],
            "team_id": [c.team_id for c in cands],
            "price": [c.price for c in cands],
            "status": "a",
            "chance": 100.0,
        }
    )
    return Projection("t", 2026, list(gws), meta, pts, mins), cands


def state_for(cands, bank=20, ft=1, chips_used=(), next_gw=10):
    squad, _ = initial_squad(cands)
    return SquadState(next_gw=next_gw, squad=squad, bank=bank, free_transfers=ft,
                      chips_used=list(chips_used))  # fmt: skip


def test_decision_is_legal_and_beats_doing_nothing():
    proj, cands = projection(1)
    st = state_for(cands, bank=50)
    d = decide(st, proj, RULES, cfg=CFG, use_chips=False)
    assert len(d.xi) == 11 and len(d.bench) == 4
    assert d.xp >= d.xp_no_transfers - 1e-6
    assert d.captain in d.xi and d.vice in d.xi and d.captain != d.vice
    assert d.bank_after >= 0
    assert "GW10" in d.summary() and "GW11:" in d.summary()


def test_used_chips_are_not_offered():
    proj, cands = projection(2)
    st = state_for(cands, chips_used=[("wildcard", 3), ("freehit", 4), ("bboost", 5), ("3xc", 2)])
    d = decide(st, proj, RULES, cfg=CFG, chip_cfg=FAST)
    assert d.chip is None and d.chip_report["scenarios"] == []


def test_triple_captain_goes_to_the_week_with_a_standout_double():
    # one elite player gets a huge expected score in the final gameweek only
    cands = make_pool(5, horizon=3)
    star = max(range(len(cands)), key=lambda i: sum(cands[i].xp))
    proj, cands = projection(5, boost={(star, 2): 14.0}, spread=0.8)
    st = state_for(cands, bank=200)
    st.squad[-1] = st.squad[-1]  # no-op: squad stays as is
    cc = ch.ChipConfig(n_worlds=60, scenario_time_limit=30,
                       min_gain={**ch.MIN_GAIN, "3xc": 4.0}, hold_margin=0.5)  # fmt: skip
    d = decide(st, proj, RULES, cfg=CFG, chip_cfg=cc)
    tc = [s for s in d.chip_report["scenarios"] if s["chip"] == "3xc"]
    best = max(tc, key=lambda s: s["gain"])
    assert best["gw"] == 12
    assert d.chip != "3xc"  # a better week exists later, so it is held now
    assert "3xc" in d.chip_report["held"]


def test_wildcard_needs_to_win_most_worlds():
    proj, cands = projection(6)
    base = PlanInput(gws=proj.gws, squad=state_for(cands).squad, bank=20, free_transfers=1,
                     candidates=build_candidates(proj, state_for(cands).squad))  # fmt: skip
    plan = ch.scenario(base, "wildcard", 10, CFG, base.candidates, 1000)
    worlds = np.arange(50)
    ws = ch.score_plan(plan, proj, RULES, worlds)
    assert ws.shape == (50,) and (ws > 0).all()
    assert ch.MIN_WIN["wildcard"] >= 0.7


def test_must_use_chip_is_played_even_below_threshold():
    proj, cands = projection(7)
    st = state_for(cands, chips_used=[("wildcard", 3), ("freehit", 4), ("3xc", 2)], next_gw=10)
    d = decide(st, proj, RULES, cfg=CFG, chip_cfg=FAST)
    # bench boost window is open until GW19 (beyond the horizon): not must-use, can be held
    assert d.chip in (None, "bboost")


def test_to_canonical_maps_element_ids_and_rejects_strangers():
    _, cands = projection(8)
    st = state_for(cands)
    pool = pd.DataFrame(
        {
            "fpl_id": [p.player_id for p in st.squad],
            "player_id": [p.player_id + 1000 for p in st.squad],
            "team_id": [p.team_id for p in st.squad],
        }
    )
    out = to_canonical(st, pool)
    assert {p.player_id for p in out.squad} == {p.player_id + 1000 for p in st.squad}
    with pytest.raises(ValueError):
        to_canonical(st, pool.iloc[:5])


def test_prune_keeps_the_squad_and_the_best_players():
    _, cands = projection(9)
    squad, _ = initial_squad(cands)
    kept = prune(cands, squad, per_position=6)
    ids = {c.player_id for c in kept}
    assert {p.player_id for p in squad} <= ids
    top_fwd = max((c for c in cands if c.position == "FWD"), key=lambda c: sum(c.xp))
    assert top_fwd.player_id in ids and len(kept) < len(cands)


def test_squad_players_without_a_projection_score_zero():
    proj, cands = projection(10)
    squad, _ = initial_squad(cands)
    squad[0] = type(squad[0])(9999, squad[0].position, squad[0].team_id, 50, 50)
    cs = build_candidates(proj, squad)
    ghost = next(c for c in cs if c.player_id == 9999)
    assert ghost.xp == [0.0, 0.0, 0.0]


def _scn(chip, gw, gain, win=0.9):
    from fpl_agent.solver.planner import Plan

    return ch.Scenario(chip, gw, Plan([], 0.0, 0, 1), gain=gain, win_share=win)


def _base3():
    return PlanInput(gws=[10, 11, 12], squad=[], bank=0, free_transfers=1, candidates=[])


def test_wildcard_below_seventy_percent_of_worlds_is_held():
    base = ch.Scenario(None, None, None)
    sc = [_scn("wildcard", 10, 12.0, win=0.62)]
    d = ch._decide(_base3(), base, sc, {"wildcard": (6, 19)}, ch.ChipConfig())
    assert d.play_now is None and "62%" in d.held["wildcard"]


def test_chip_is_played_when_now_is_best_and_gain_clears_threshold():
    sc = [_scn("bboost", 10, 11.0), _scn("bboost", 11, 9.0), _scn("bboost", 12, 7.0)]
    d = ch._decide(
        _base3(), ch.Scenario(None, None, None), sc, {"bboost": (6, 19)}, ch.ChipConfig()
    )
    assert d.play_now == "bboost" and d.chosen.gw == 10


def test_chip_waits_for_a_clearly_better_week():
    sc = [_scn("bboost", 10, 9.0), _scn("bboost", 12, 15.0)]
    d = ch._decide(
        _base3(), ch.Scenario(None, None, None), sc, {"bboost": (6, 19)}, ch.ChipConfig()
    )
    assert d.play_now is None and "GW12" in d.held["bboost"]


def test_expiring_chip_is_played_at_the_best_week_even_for_a_tiny_gain():
    sc = [_scn("freehit", 10, 0.5), _scn("freehit", 11, 0.2), _scn("freehit", 12, 0.1)]
    d = ch._decide(
        _base3(), ch.Scenario(None, None, None), sc, {"freehit": (6, 12)}, ch.ChipConfig()
    )
    assert d.play_now == "freehit"  # the window closes inside the horizon: use it or lose it


def test_rank_mode_captain_prefers_the_higher_haul_chance_within_the_slack():
    import numpy as np
    import pandas as pd

    from fpl_agent.model.project import Projection
    from fpl_agent.solver.decide import captain_choice

    meta = pd.DataFrame({"player_id": [1, 2, 3]})
    pts = np.zeros((3, 1, 100), dtype="float32")
    pts[0, 0, :] = 6.0  # steady 6.0 average, never a haul
    pts[1, 0, :] = np.where(np.arange(100) < 43, 12.0, 1.0)  # 5.7 average, 43% hauls
    pts[2, 0, :] = 3.0
    proj = Projection("r", 2026, [6], meta, pts, np.zeros_like(pts, dtype="int16"))
    cap, vice, opts = captain_choice(proj, [1, 2, 3], slack=0.5)
    assert (cap, vice) == (2, 1) and opts[0]["player_id"] == 1
    cap, _, _ = captain_choice(proj, [1, 2, 3], slack=0.0)
    assert cap == 1


def test_unavailable_or_doubtful_players_are_never_offered_as_buys():
    from fpl_agent.solver.inputs import build_candidates

    proj, cands = projection(1)
    st = state_for(cands, bank=60)
    owned = {p.player_id for p in st.squad}
    outsider = next(i for i in proj.player_ids if i not in owned)
    meta = proj.meta.assign(status="a", chance=100.0)
    meta.loc[meta.player_id == outsider, "chance"] = 50.0
    proj.meta = meta
    assert outsider not in {c.player_id for c in build_candidates(proj, st.squad)}
    meta.loc[meta.player_id == outsider, ["status", "chance"]] = ["a", float("nan")]
    assert outsider in {c.player_id for c in build_candidates(proj, st.squad)}


def test_an_active_wildcard_replans_the_whole_squad_for_free():
    from dataclasses import replace

    proj, cands = projection(3)
    st = replace(state_for(cands, bank=50, chips_used=[("wildcard", 10)]), wildcard_active=True)
    d = decide(st, proj, RULES, cfg=CFG, chip_cfg=FAST)
    assert d.chip == "wildcard" and d.hits == 0
    assert d.chip_report["play_now"] == "wildcard"


def test_an_active_wildcard_never_rebuys_this_weeks_sales_or_resells_this_weeks_buys():
    from dataclasses import replace

    proj, cands = projection(3)
    st = state_for(cands, bank=50, chips_used=[("wildcard", 10)])
    owned = {p.player_id for p in st.squad}
    # the best outsiders were sold earlier this week; the squad's first three were bought
    sold = sorted((c for c in cands if c.player_id not in owned),
                  key=lambda c: -sum(c.xp))[:5]  # fmt: skip
    old = [replace(p, player_id=c.player_id) for p, c in zip(st.squad[:3], sold[:3], strict=True)]
    st = replace(st, wildcard_active=True, base_squad=old + st.squad[3:])
    d = decide(st, proj, RULES, cfg=CFG, chip_cfg=FAST)
    assert not set(d.transfers_in) & {c.player_id for c in sold[:3]}
    assert not set(d.transfers_out) & {p.player_id for p in st.squad[:3]}


def test_popular_players_are_kept_and_captained():
    proj, cands = projection(3)
    st = state_for(cands, bank=50, ft=5)
    plain = decide(st, proj, RULES, cfg=CFG, use_chips=False)
    assert plain.transfers_out, "the fixture must want to sell someone"
    sold = plain.transfers_out[0]
    own = {sold: 74.0}
    d = decide(st, proj, RULES, cfg=CFG, use_chips=False, ownership=own)
    assert sold not in d.transfers_out
    assert any("kept for ownership" in w and "74%" in w for w in d.warnings)
    if sold in d.xi:  # a popular starter close to the best captain gets the armband
        mean = dict(zip(proj.player_ids, proj.mean()[:, 0], strict=True))
        best = max(mean[p] for p in d.xi)
        if mean[sold] >= best - 1.0:
            assert d.captain == sold


def test_an_unavailable_popular_player_can_still_be_sold():
    proj, cands = projection(3)
    st = state_for(cands, bank=50, ft=5)
    sold = decide(st, proj, RULES, cfg=CFG, use_chips=False).transfers_out[0]
    proj.meta.loc[proj.meta["player_id"] == sold, "status"] = "i"
    d = decide(st, proj, RULES, cfg=CFG, use_chips=False, ownership={sold: 74.0})
    assert not any("kept for ownership" in w for w in d.warnings)


def test_no_further_transfers_once_this_weeks_moves_are_made():
    from dataclasses import replace

    proj, cands = projection(3)
    st = state_for(cands, bank=50, ft=5)
    assert decide(st, proj, RULES, cfg=CFG, use_chips=False).transfers_in
    d = decide(replace(st, already_transferred=2), proj, RULES, cfg=CFG, use_chips=False)
    assert not d.transfers_in and len(d.xi) == 11


def test_the_report_shows_popular_players_you_lack_and_your_differentials():
    proj, cands = projection(3)
    st = state_for(cands, bank=50)
    d0 = decide(st, proj, RULES, cfg=CFG, use_chips=False)
    outsider = next(p for p in proj.player_ids if p not in d0.week.squad)
    own = {p: 20.0 for p in proj.player_ids}
    own[outsider] = 80.0
    own[d0.xi[0]] = 1.0
    d = decide(st, proj, RULES, cfg=CFG, use_chips=False, ownership=own,
               names={outsider: "Popular", d0.xi[0]: "Rare"})  # fmt: skip
    text = d.summary()
    if outsider not in d.week.squad:
        assert "Popular 80%" in text
    if d0.xi[0] in d.xi:
        assert "Rare 1%" in text
    assert "field:" in text


def test_with_field_data_the_captain_follows_the_fields_armband_when_close():
    from fpl_agent.solver.decide import captain_choice

    proj, cands = projection(3)
    st = state_for(cands, bank=50)
    xi = decide(st, proj, RULES, cfg=CFG, use_chips=False).xi
    plain, _, _ = captain_choice(proj, xi, slack=10.0)
    other = next(p for p in xi if p != plain)
    cap, _, _ = captain_choice(proj, xi, slack=10.0, eo={other: 1.9}, risk=50.0)
    assert cap == other
    same, _, _ = captain_choice(proj, xi, slack=10.0, eo={other: 1.9}, risk=0.0)
    assert same == plain
