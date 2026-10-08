"""Team strength and odds: parameters of a synthetic league are recovered, the odds inversion
round-trips, and a promoted club starts below an established one."""

from datetime import date

import numpy as np
import pytest

from fpl_agent.model import odds, strength
from tests.synth import build_world

AS_OF = date(2030, 1, 1)


@pytest.fixture(scope="module")
def world():
    return build_world(n_teams=10, seasons=(2024, 2025), seed=3, players=False)


@pytest.fixture(scope="module")
def ratings(world):
    m = strength.load_matches(world.db)
    return strength.fit_ratings(m, AS_OF, half_life_days=4000, ridge=0.5), m


def test_recovers_attack_and_defence(world, ratings):
    r, _ = ratings
    ids = list(world.teams)
    for key, est in (("att", r.att), ("dfn", r.dfn)):
        truth = np.array([world.teams[i][key] for i in ids])
        got = np.array([est[i] for i in ids])
        assert np.corrcoef(truth, got)[0, 1] > 0.75, key


def test_predicted_scoring_level_matches_truth(world, ratings):
    r, m = ratings
    pred = np.mean([sum(r.lambdas(h, a)) for h, a in zip(m.home_id, m.away_id, strict=True)])
    actual = (m.hg + m.ag).mean()
    assert pred == pytest.approx(actual, rel=0.1)
    assert r.home == pytest.approx(world.home, abs=0.15)


def test_stronger_team_is_favoured_at_home(world, ratings):
    r, _ = ratings
    ids = sorted(world.teams, key=lambda i: world.teams[i]["att"] - world.teams[i]["dfn"])
    weak, strong = ids[0], ids[-1]
    p_home = strength.outcome_probs(*r.lambdas(strong, weak))
    p_away = strength.outcome_probs(*r.lambdas(weak, strong))
    assert p_home["home"] > p_away["home"]
    assert p_home["home_cs"] > p_away["home_cs"]
    assert sum(p_home[k] for k in ("home", "draw", "away")) == pytest.approx(1.0, abs=1e-3)


def test_time_decay_favours_recent_form():
    w = build_world(n_teams=6, seasons=(2024,), seed=5, players=False)
    m = strength.load_matches(w.db)
    old = strength.fit_ratings(m, date(2024, 8, 1) + (date(2025, 6, 1) - date(2024, 8, 1)),
                               half_life_days=20)  # fmt: skip
    flat = strength.fit_ratings(m, date(2025, 6, 1), half_life_days=100000)
    assert old.att != flat.att  # decay changes the estimate


def test_no_matches_gives_a_neutral_model():
    r = strength.fit_ratings(strength.load_matches(build_world(2, (2024,), players=False).db),
                             date(2000, 1, 1))  # fmt: skip
    lh, la = r.lambdas(1, 2)
    assert 0.5 < la < lh < 3.0 and r.att == {}


def test_unknown_team_is_average(ratings):
    r, _ = ratings
    lh, la = r.lambdas(9999, 8888)
    assert lh > la > 0


def test_promoted_team_prior_is_below_established_clubs():
    """A club seen only in tier 2 gets weaker attack and leakier defence than a tier-1 club."""
    import pandas as pd

    rows = []
    d0 = date(2025, 8, 1)
    for i in range(40):
        d = date.fromordinal(d0.toordinal() + i * 3)
        rows.append((d, "ENG1", 1, 1, 2, 1, 1, 1.2, 1.1))
        rows.append((d, "ENG1", 1, 2, 1, 1, 1, 1.1, 1.2))
        rows.append((d, "ENG2", 2, 3, 4, 1, 1, 1.1, 1.1))
        rows.append((d, "ENG2", 2, 4, 3, 1, 1, 1.1, 1.1))
    m = pd.DataFrame(rows, columns=strength.MATCH_COLUMNS)
    r = strength.fit_ratings(m, date(2026, 8, 1), half_life_days=10000, ridge=2.0)
    assert r.att[3] < r.att[1] and r.dfn[3] > r.dfn[1]
    assert r.tier_of[3] == 2 and r.tier_of[1] == 1


def test_matches_after_as_of_are_not_used(world):
    m = strength.load_matches(world.db)
    early = strength.fit_ratings(m, date(2024, 9, 1))
    assert len(early.att) > 0
    assert (
        strength.load_matches(world.db, as_of=date(2024, 8, 11)).shape[0] <= len(world.teams) // 2
    )


# ---- odds -------------------------------------------------------------------------------
def odds_for(lh, la, margin=0.06):
    mat = strength.goal_matrix(lh, la)
    p = [np.tril(mat, -1).sum(), np.trace(mat), np.triu(mat, 1).sum()]
    return [1 / (x * (1 + margin)) for x in p]


@pytest.mark.parametrize("lh,la", [(1.8, 0.9), (1.2, 1.2), (0.8, 1.9), (2.6, 0.5)])
def test_odds_roundtrip(lh, la):
    h, d, a = odds_for(lh, la)
    got = odds.implied_lambdas(h, d, a)
    # 1X2 alone fixes the goal difference well but the total only loosely
    assert got[0] - got[1] == pytest.approx(lh - la, abs=0.35)
    assert got[0] > got[1] if lh > la else got[0] <= got[1] + 0.05


def test_odds_totals_pin_down_the_goal_level():
    lh, la = 1.9, 1.1
    h, d, a = odds_for(lh, la)
    p_over = odds.p_over(lh, la)
    ov, un = 1 / (p_over * 1.05), 1 / ((1 - p_over) * 1.05)
    got = odds.implied_lambdas(h, d, a, ov, un)
    assert got[0] == pytest.approx(lh, abs=0.15) and got[1] == pytest.approx(la, abs=0.15)


def test_remove_margin_and_invalid_odds():
    assert sum(odds.remove_margin(2.0, 3.5, 4.0)) == pytest.approx(1.0)
    for bad in ((0.9, 3, 3), (None, 3, 3), (float("nan"), 3, 3)):
        with pytest.raises(ValueError):
            odds.remove_margin(*bad)


def test_blend_prefers_market_when_weighted_and_falls_back_without_odds():
    model, market = (1.0, 1.0), (2.0, 0.5)
    assert odds.blend(model, None) == model
    assert odds.blend(model, market, 0.0) == pytest.approx(model)
    assert odds.blend(model, market, 1.0) == pytest.approx(market)
    mid = odds.blend(model, market, 0.5)
    assert 1.0 < mid[0] < 2.0 and 0.5 < mid[1] < 1.0
