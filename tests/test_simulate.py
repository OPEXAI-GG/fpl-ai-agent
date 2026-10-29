"""The match simulator: its scoring agrees with the rules engine on every simulated event, bonus
points follow FPL's tie rules, and the joint structure (shared clean sheets, goals allocated from
team totals) behaves the way football does."""

import numpy as np
import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from fpl_agent.model.simulate import PlayerSpec, SimConfig, bonus_from_bps, simulate_fixture
from fpl_agent.rules import Rules, bonus, score

RULES = Rules.load(2026)


def team(prefix: int, shares=(0.25, 0.1, 0.05)):
    """A full first team: 1 GK, 4 DEF, 4 MID, 2 FWD all starting, plus a bench player of each kind."""
    specs = [PlayerSpec(prefix + 1, "GKP", 0.92, 0.95, 0.01, xg_share=0.0, xa_share=0.0, cbit_mu=1)]
    for i in range(4):
        specs.append(PlayerSpec(prefix + 10 + i, "DEF", 0.9, 0.9, 0.02, xg_share=0.03,
                                xa_share=0.04, cbit_mu=7.5))  # fmt: skip
    for i in range(4):
        specs.append(PlayerSpec(prefix + 20 + i, "MID", 0.88, 0.85, 0.05, xg_share=0.1 + 0.02 * i,
                                xa_share=0.12, cbit_mu=8))  # fmt: skip
    for i in range(2):
        specs.append(PlayerSpec(prefix + 30 + i, "FWD", 0.85, 0.85, 0.08, xg_share=shares[0] - 0.05 * i,
                                xa_share=0.08, cbit_mu=5))  # fmt: skip
    specs.append(PlayerSpec(prefix + 40, "MID", 0.1, 0.5, 0.4, xg_share=0.08, xa_share=0.08))
    specs.append(PlayerSpec(prefix + 41, "GKP", 0.08, 0.9, 0.0, xg_share=0.0, xa_share=0.0))
    return specs


@pytest.fixture(scope="module")
def sim():
    cfg = SimConfig(n=6000, seed=1, keep_events=True)
    return simulate_fixture(team(100), team(200), 1.6, 1.0, RULES, cfg), cfg


def idx(sim_, pid):
    return sim_.player_ids.index(pid)


def test_team_goals_have_the_requested_mean(sim):
    s, _ = sim
    assert s.goals_for["home"].mean() == pytest.approx(1.6, abs=0.06)
    assert s.goals_for["away"].mean() == pytest.approx(1.0, abs=0.06)
    # overdispersed relative to Poisson: variance above the mean
    assert s.goals_for["home"].var() > s.goals_for["home"].mean() * 1.02


def test_every_team_goal_is_given_to_a_player(sim):
    s, _ = sim
    n_home = len(team(100))
    scored_home = s.events["goals"][:, :n_home].sum(1)
    assert (scored_home == s.goals_for["home"]).mean() > 0.995


def test_points_match_the_rules_engine_event_by_event(sim):
    """The simulator's vectorised scoring must equal engine.score on the same events."""
    s, _ = sim
    ev = s.events
    specs = team(100) + team(200)
    rng = np.random.default_rng(5)
    for _ in range(400):
        i, j = int(rng.integers(0, len(ev["minutes"]))), int(rng.integers(0, len(specs)))
        pos = specs[j].position
        stats = {
            "minutes": int(ev["minutes"][i, j]), "goals_scored": int(ev["goals"][i, j]),
            "assists": int(ev["assists"][i, j]), "clean_sheets": int(ev["clean_sheets"][i, j]),
            "goals_conceded": int(ev["conceded"][i, j]), "saves": int(ev["saves"][i, j]),
            "yellow_cards": int(ev["yellow"][i, j]), "red_cards": int(ev["red"][i, j]),
            "own_goals": int(ev["og"][i, j]), "penalties_missed": int(ev["pen_missed"][i, j]),
            "penalties_saved": int(ev["pen_saved"][i, j]), "bonus": int(ev["bonus"][i, j]),
            "defensive_contribution": int(ev["count"][i, j]),
        }  # fmt: skip
        assert score(stats, pos, RULES)["total"] == pytest.approx(s.points[i, j]), (pos, stats)


def test_clean_sheet_probability_for_a_full_match_goalkeeper(sim):
    s, cfg = sim
    gk = idx(s, 101)
    k, lam = cfg.dispersion, 1.0
    p0 = (k / (k + lam)) ** k  # P(0 goals) under the gamma-mixed Poisson
    played_full = s.minutes[:, gk] >= 60
    cs = (s.events["clean_sheets"][:, gk] > 0)[played_full].mean()
    assert cs == pytest.approx(p0, abs=0.03)


def test_goalkeeper_saves_scale_with_opposition_threat():
    cfg = SimConfig(n=4000, seed=2)
    weak = simulate_fixture(team(100), team(200), 1.0, 0.6, RULES, cfg)
    strong = simulate_fixture(team(100), team(200), 1.0, 2.2, RULES, cfg)
    gk_w, gk_s = weak.components["saves"][0], strong.components["saves"][0]
    assert gk_s > gk_w * 1.5


def test_defender_and_goalkeeper_share_the_clean_sheet(sim):
    s, _ = sim
    gk, d1 = idx(s, 101), idx(s, 110)
    cs_gk = s.events["clean_sheets"][:, gk].astype(float)
    cs_d = s.events["clean_sheets"][:, d1].astype(float)
    assert np.corrcoef(cs_gk, cs_d)[0, 1] > 0.6
    # ...and a striker's points are not tied to his own keeper's clean sheet
    fw = idx(s, 130)
    assert abs(np.corrcoef(s.points[:, gk], s.points[:, fw])[0, 1]) < 0.35


def test_bonus_totals_and_tie_structure(sim):
    s, _ = sim
    b = s.events["bonus"]
    assert b.max() == 3 and b.min() == 0
    totals = b.sum(1)
    assert totals.min() >= 3 and totals.mean() == pytest.approx(6.0, abs=0.35)
    # most bonus goes to the players who scored and created
    n_home = len(team(100))
    scorers = s.events["goals"][:, :n_home] > 0
    assert b[:, :n_home][scorers].mean() > b[:, :n_home][~scorers].mean()


@settings(max_examples=120, deadline=None)
@given(st.lists(st.integers(0, 40), min_size=4, max_size=22))
def test_bonus_matches_the_engine_including_ties(values):
    bps = np.array([values], dtype=float)
    played = np.ones_like(bps, dtype=bool)
    got = bonus_from_bps(bps, played)[0]
    want = bonus({i: v for i, v in enumerate(values)}, RULES)
    assert {i: int(g) for i, g in enumerate(got) if g} == want


def test_non_playing_players_never_earn_bonus():
    bps = np.array([[30.0, 25.0, 20.0, 99.0]])
    played = np.array([[True, True, True, False]])
    assert bonus_from_bps(bps, played)[0].tolist() == [3, 2, 1, 0]


def test_more_minutes_and_more_share_mean_more_points():
    cfg = SimConfig(n=3000, seed=3)
    base = simulate_fixture(team(100), team(200), 1.5, 1.2, RULES, cfg).points.mean(0)
    sims = {}
    for p_start in (0.2, 0.95):
        t = team(100)
        t[9].p_start = p_start  # a forward
        sims[p_start] = simulate_fixture(t, team(200), 1.5, 1.2, RULES, cfg).points.mean(0)[9]
    assert sims[0.95] > sims[0.2] * 2
    t = team(100)
    t[9].xg_share = 0.45
    boosted = simulate_fixture(t, team(200), 1.5, 1.2, RULES, cfg).points.mean(0)[9]
    assert boosted > base[9] * 1.15


def test_a_stronger_attack_means_more_points_for_attackers_and_fewer_for_the_other_defence():
    cfg = SimConfig(n=3000, seed=4)
    a = simulate_fixture(team(100), team(200), 2.4, 0.7, RULES, cfg)
    b = simulate_fixture(team(100), team(200), 0.9, 1.6, RULES, cfg)
    n_home = len(team(100))
    assert a.points[:, 9].mean() > b.points[:, 9].mean()  # home forward
    assert a.points[:, n_home + 1].mean() < b.points[:, n_home + 1].mean()  # away defender


def test_defcon_probability_rises_with_the_defensive_action_rate():
    cfg = SimConfig(n=4000, seed=6)
    lo, hi = team(100), team(100)
    lo[1].cbit_mu, hi[1].cbit_mu = 4.0, 11.0
    p_lo = simulate_fixture(lo, team(200), 1.4, 1.4, RULES, cfg).components["defcon"][1]
    p_hi = simulate_fixture(hi, team(200), 1.4, 1.4, RULES, cfg).components["defcon"][1]
    assert p_hi > p_lo * 3


def test_exactly_one_goalkeeper_plays():
    cfg = SimConfig(n=2000, seed=7, keep_events=True)
    s = simulate_fixture(team(100), team(200), 1.4, 1.4, RULES, cfg)
    n_home = len(team(100))
    gks = [i for i, sp in enumerate(team(100)) if sp.position == "GKP"]
    starters = (s.minutes[:, gks] >= 1).sum(1)
    assert (starters == 1).mean() > 0.97 and n_home == 13


def test_same_seed_same_result_and_different_seed_differs():
    a = simulate_fixture(team(100), team(200), 1.4, 1.4, RULES, SimConfig(n=500, seed=9))
    b = simulate_fixture(team(100), team(200), 1.4, 1.4, RULES, SimConfig(n=500, seed=9))
    c = simulate_fixture(team(100), team(200), 1.4, 1.4, RULES, SimConfig(n=500, seed=10))
    assert np.array_equal(a.points, b.points) and not np.array_equal(a.points, c.points)


def _keeper_starts(p_starts, n=40_000):
    from fpl_agent.model.simulate import _minutes

    specs = [PlayerSpec(i, "GKP", p, 1.0, 0.0) for i, p in enumerate(p_starts)]
    return _minutes(np.random.default_rng(3), specs, n, SimConfig()) > 0


def test_near_zero_keepers_are_not_promoted_to_starters():
    played = _keeper_starts([0.0001, 0.0001])  # used to be ~0.5 each
    assert played.mean(axis=0).max() < 0.002


def test_a_doubtful_first_choice_keeper_keeps_his_own_start_chance():
    played = _keeper_starts([0.6, 0.1])  # used to be renormalised to ~0.86 / ~0.14
    assert np.allclose(played.mean(axis=0), [0.6, 0.1], atol=0.01)
    assert played.sum(axis=1).max() == 1  # never two keepers


def test_keeper_chances_above_one_still_give_exactly_one_starter():
    played = _keeper_starts([0.95, 0.3])
    assert (played.sum(axis=1) == 1).all()
    assert np.allclose(played.mean(axis=0), [0.95 / 1.25, 0.3 / 1.25], atol=0.01)


def _sheet(specs, n=20000, seed=0):
    from fpl_agent.model.simulate import _minutes

    mins = _minutes(np.random.default_rng(seed), specs, n, SimConfig())
    return mins, np.array([s.position == "GKP" for s in specs])


def test_a_team_sheet_never_has_more_than_ten_outfield_starters_or_five_subs():
    """Audit case: 19 outfield players whose start chances sum above ten. Independent draws
    put more than ten of them on the pitch in ~41% of worlds (up to 19); a legal sheet never
    has more than ten outfield starters, one keeper and five substitutes."""
    rng = np.random.default_rng(0)
    assert ((rng.random((20000, 19)) < 10 / 19).sum(1) > 10).mean() > 0.3  # the old defect
    keeper = [PlayerSpec(1, "GKP", 0.95, 0.95, 0.0)]
    # no cameos: everyone on the pitch started
    mins, gk = _sheet(keeper + [PlayerSpec(10 + i, "MID", 0.55, 0.8, 0.0) for i in range(19)])
    starters = (mins[:, ~gk] > 0).sum(1)
    assert starters.max() == 10 and starters.mean() > 9.0
    assert ((mins[:, gk] > 0).sum(1) <= 1).all()
    # a sure starter is (almost) never the one left out
    sure = keeper + [PlayerSpec(2, "FWD", 1.0, 0.9, 0.0)]
    mins, _ = _sheet(sure + [PlayerSpec(10 + i, "MID", 0.6, 0.8, 0.0) for i in range(19)])
    assert (mins[:, 1] > 0).mean() > 0.99
    # substitutes: twelve likely cameos, never more than five come on
    bench = keeper + [PlayerSpec(10 + i, "DEF", 0.9, 0.9, 0.0) for i in range(10)]
    bench += [PlayerSpec(40 + i, "MID", 0.0, 0.0, 0.8) for i in range(12)]
    mins, _ = _sheet(bench)
    assert (mins[:, 11:] > 0).sum(1).max() == 5
