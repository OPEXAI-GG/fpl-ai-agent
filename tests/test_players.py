"""Player rates and league translation, checked against the synthetic world's known truth."""

import numpy as np
import pandas as pd
import pytest

from fpl_agent.db.database import FootballDB
from fpl_agent.model import features, players, translation
from tests.synth import build_world


@pytest.fixture(scope="module")
def world():
    return build_world(n_teams=10, seasons=(2023, 2024), seed=21)


@pytest.fixture(scope="module")
def inputs(world):
    pm = features.match_frame(world.db)
    frame = features.player_gw_frame(world.db)
    positions = {p: t["pos"] for p, t in world.players.items()}
    return pm, frame, positions


def test_match_frame_has_stats_and_team_exposure(inputs):
    pm, _, _ = inputs
    assert {"xg", "xa", "cbit", "cbirt", "team_xg", "minutes", "comp", "season"} <= set(pm.columns)
    played = pm[pm.minutes > 0]
    assert played.xg.notna().all() and played.team_xg.notna().all()
    assert pm.groupby("match_id").size().min() >= 20


def test_attacking_share_recovers_who_takes_the_chances(world, inputs):
    pm, frame, positions = inputs
    rates = players.fit_player_rates(pm, frame, positions, current_season=2024)
    truth = pd.Series({p: t["goal_w"] for p, t in world.players.items()})
    est = rates.set_index("player_id")["xg_share"]
    outfield = [p for p, t in world.players.items() if t["pos"] != "GKP"]
    assert np.corrcoef(truth[outfield], est[outfield])[0, 1] > 0.85
    # shares of a full team's outfield players add up to roughly the whole team's xG
    team = world.players[outfield[0]]["team"]
    members = [p for p in outfield if world.players[p]["team"] == team]
    starters_share = est[members].sum()
    assert 0.5 < starters_share < 2.5


def test_defcon_rate_and_dispersion(world, inputs):
    pm, frame, positions = inputs
    rates = players.fit_player_rates(pm, frame, positions, current_season=2024).set_index("player_id")
    defenders = [p for p, t in world.players.items() if t["pos"] == "DEF"]
    truth = np.array([world.players[p]["cbit_mu"] for p in defenders])
    assert np.corrcoef(truth, rates.loc[defenders, "cbit_mu"])[0, 1] > 0.8
    assert rates.loc[defenders, "cbit_mu"].mean() == pytest.approx(truth.mean(), rel=0.15)
    assert (rates.cbit_r > 1).all()


def test_shrinkage_moves_thin_evidence_toward_the_position_mean(world, inputs):
    pm, frame, positions = inputs
    forward = next(p for p, t in world.players.items() if t["pos"] == "FWD")
    thin = pm[(pm.player_id != forward) | (pm.date < pm.date.min() + pd.Timedelta(days=14))]
    full = players.fit_player_rates(pm, frame, positions, 2024).set_index("player_id")
    little = players.fit_player_rates(thin, frame, positions, 2024).set_index("player_id")
    prior = players.position_priors(pm)["share"]["FWD"]
    assert abs(little.loc[forward, "xg_share"] - prior) <= abs(full.loc[forward, "xg_share"] - prior) + 1e-9


def test_unknown_player_gets_the_position_prior(inputs):
    pm, frame, _ = inputs
    out = players.fit_player_rates(pm, frame, {10**7: "MID"}, 2024).iloc[0]
    assert out.source == "prior" and out.evidence_minutes == 0
    assert out.xg_share == pytest.approx(players.position_priors(pm)["share"]["MID"], rel=1e-6)


def test_recent_seasons_count_more():
    rows = []
    for season, xg in ((2022, 1.2), (2024, 0.2)):  # an old hot season, a recent cold one
        for i in range(20):
            rows.append({"match_id": season * 100 + i, "player_id": 1, "team_id": 1, "comp": "ENG1",
                         "season": season, "date": pd.Timestamp(season, 9, 1), "minutes": 90,
                         "position": "FWD", "team_xg": 1.5, "xg": xg * 0.5, "xa": 0.1, "cbit": 2.0,
                         "cbirt": 3.0, "goals": 0, "assists": 0, "yellow_cards": 0, "red_cards": 0})
    pm = pd.DataFrame(rows)
    frame = pd.DataFrame({"player_id": [1], "minutes": [90], "yellow": [0.0], "red": [0.0]})
    new = players.fit_player_rates(pm, frame, {1: "FWD"}, 2024).iloc[0].xg_share
    same_year = players.fit_player_rates(pm, frame, {1: "FWD"}, 2022).iloc[0].xg_share
    assert new < same_year  # judged in 2024 the cold recent season dominates


def test_cards_shrink_to_position_mean(inputs):
    pm, frame, positions = inputs
    rates = players.fit_player_rates(pm, frame, positions, 2024)
    assert rates.yellow90.between(0, 1).all() and rates.red90.between(0, 0.2).all()


# ---- league translation -----------------------------------------------------------------
def movers(n=30, factor=0.6, seed=0, minutes=900):
    """Players whose xG share in the Championship is 1/factor of their Premier League share."""
    rng = np.random.default_rng(seed)
    rows = []
    for pid in range(n):
        base = rng.uniform(0.04, 0.2)
        for comp, mult in (("ENG2", 1 / factor), ("ENG1", 1.0)):
            for i in range(minutes // 90):
                rows.append({"match_id": hash((pid, comp, i)) % 10**9, "player_id": pid,
                             "team_id": 1, "comp": comp, "season": 2024,
                             "date": pd.Timestamp("2024-09-01"), "minutes": 90, "position": "MID",
                             "team_xg": 1.5, "xg": base * mult * 1.5, "xa": base * mult * 0.7 * 1.5,
                             "cbit": 6.0 * (1.2 if comp == "ENG2" else 1.0), "cbirt": 7.0,
                             "goals": 0, "assists": 0, "yellow_cards": 0, "red_cards": 0})
    return pd.DataFrame(rows)


def test_translation_learns_the_factor_from_movers():
    est = translation.estimate(movers(factor=0.6)).set_index(["from_comp", "stat"])
    assert est.loc[("ENG2", "xg_share"), "factor"] == pytest.approx(0.6, abs=0.06)
    assert est.loc[("ENG2", "cbit"), "factor"] == pytest.approx(1 / 1.2, abs=0.06)
    assert est.loc[("ENG2", "xg_share"), "n_players"] == 30


def test_translation_without_movers_falls_back_to_the_prior():
    pm = movers()
    pm = pm[pm.comp == "ENG2"]  # nobody moved
    est = translation.estimate(pm).set_index(["from_comp", "stat"])
    assert est.loc[("ENG2", "xg_share"), "factor"] == pytest.approx(translation.prior("ENG2"))
    assert est.loc[("ENG2", "xg_share"), "n_players"] == 0


def test_thin_evidence_is_pulled_toward_the_prior():
    thin = translation.estimate(movers(n=2, factor=0.3, minutes=450)).set_index(["from_comp", "stat"])
    thick = translation.estimate(movers(n=60, factor=0.3)).set_index(["from_comp", "stat"])
    p = translation.prior("ENG2")
    f_thin, f_thick = thin.loc[("ENG2", "xg_share"), "factor"], thick.loc[("ENG2", "xg_share"), "factor"]
    assert abs(f_thin - p) < abs(f_thick - p) and f_thick == pytest.approx(0.3, abs=0.12)


def test_translation_roundtrip_through_the_database_and_lookup():
    db = FootballDB()
    table = translation.estimate(movers())
    assert translation.store(db, table) == len(table)
    tr = translation.Translations.load(db)
    assert tr.factor("ENG1", "xg_share") == 1.0
    assert tr.factor("ENG2", "xg_share") == pytest.approx(0.6, abs=0.06)
    assert tr.factor("XXX9", "cbit") == translation.PRIOR_DEFAULT


def test_translation_validation_beats_no_translation():
    res = translation.validate(movers(n=12, factor=0.5, minutes=450), min_minutes=400)
    assert res["xg_share"]["mae_translated"] < res["xg_share"]["mae_naive"]


def test_new_signing_rates_use_translated_foreign_history():
    pm = movers(n=1)  # one player: Championship + PL
    foreign = pm[pm.comp == "ENG2"].assign(player_id=999)
    tr = translation.Translations(translation.estimate(movers(n=30)))
    frame = pd.DataFrame({"player_id": [999], "minutes": [0], "yellow": [0.0], "red": [0.0]})
    raw = players.fit_player_rates(foreign, frame, {999: "MID"}, 2024)
    adj = players.fit_player_rates(foreign, frame, {999: "MID"}, 2024, translations=tr)
    assert adj.iloc[0].xg_share < raw.iloc[0].xg_share  # a Championship share is discounted
    assert adj.iloc[0].source == "translated"
