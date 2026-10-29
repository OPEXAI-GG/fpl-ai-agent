"""M02 candidate: FPL expected goals/assists as a per-stat fallback, off by default."""

import numpy as np
import pandas as pd
import pytest

from fpl_agent.model import features, players
from tests.synth import build_world

SHARED = ["player_id", "position", "xg_share", "xa_share", "finish", "cbit_mu", "cbit_r",
          "yellow90", "red90", "evidence_minutes", "source"]  # fmt: skip


@pytest.fixture(scope="module")
def world():
    return build_world(n_teams=10, seasons=(2023, 2024), seed=21)


@pytest.fixture(scope="module")
def inputs(world):
    pm = features.match_frame(world.db)
    frame = features.player_gw_frame(world.db)
    positions = {p: t["pos"] for p, t in world.players.items()}
    outfield = sorted(p for p, t in world.players.items() if t["pos"] != "GKP")
    hidden = outfield[::4]  # these lose their detailed history
    as_of = max(pd.to_datetime(frame.stats_ingested_at).max(),
                pd.to_datetime(frame.kickoff).max()) + pd.Timedelta(days=1)  # fmt: skip
    return pm, frame, positions, hidden, as_of


def _fit(pm, frame, positions, **kw):
    return players.fit_player_rates(pm, frame, positions, 2024, **kw).set_index("player_id")


def test_switch_off_is_the_baseline(inputs):
    pm, frame, positions, _, as_of = inputs
    base = _fit(pm, frame, positions)
    for kw in ({"as_of": as_of}, {"fpl_xg_fallback": True}):  # off, or on without a cutoff
        pd.testing.assert_frame_equal(_fit(pm, frame, positions, **kw)[SHARED[1:]],
                                      base[SHARED[1:]])  # fmt: skip
    assert set(base.xg_source) <= {"detailed", "prior"}


def test_fallback_fills_only_players_without_detailed_history(world, inputs):
    pm, frame, positions, hidden, as_of = inputs
    thin = pm[~pm.player_id.isin(hidden)]
    off = _fit(thin, frame, positions)
    on = _fit(thin, frame, positions, as_of=as_of, fpl_xg_fallback=True)
    assert (on.loc[hidden, "xg_source"] == "fpl").all()
    assert (on.loc[hidden, "xa_source"] == "fpl").all()
    assert (off.loc[hidden, "xg_source"] == "prior").all()
    kept = [p for p in positions if p not in hidden]
    pd.testing.assert_frame_equal(on.loc[kept, SHARED[1:]], off.loc[kept, SHARED[1:]])
    # the fallback carries real signal about who takes the chances
    truth = pd.Series({p: world.players[p]["goal_w"] for p in hidden})
    assert np.corrcoef(truth, on.loc[hidden, "xg_share"])[0, 1] > 0.5


def test_rows_after_the_cutoff_cannot_change_the_result(inputs):
    pm, frame, positions, hidden, _ = inputs
    thin = pm[~pm.player_id.isin(hidden)]
    cut = pd.to_datetime(frame.kickoff).quantile(0.5)
    ingested = pd.to_datetime(frame.stats_ingested_at)
    kickoff = pd.to_datetime(frame.kickoff)
    known = frame[(ingested <= cut) & (kickoff < cut)]
    ref = _fit(thin, known, positions, as_of=cut, fpl_xg_fallback=True)
    late = frame.copy()
    after = (ingested > cut) | (kickoff >= cut)
    late.loc[after, ["xg", "xa"]] = late.loc[after, ["xg", "xa"]] * 10 + 3
    got = _fit(thin, late, positions, as_of=cut, fpl_xg_fallback=True)
    cols = ["xg_share", "xa_share", "xg_source", "xa_source"]
    pd.testing.assert_frame_equal(got.loc[hidden, cols], ref.loc[hidden, cols])
    # a row kicked off before the cutoff but ingested after it is not usable either
    late_ingest = frame.copy()
    late_ingest["stats_ingested_at"] = cut + pd.Timedelta(seconds=1)
    none = _fit(thin, late_ingest, positions, as_of=cut, fpl_xg_fallback=True)
    assert (none.loc[hidden, "xg_source"] == "prior").all()


def test_goal_and_assist_fallbacks_are_independent(inputs):
    pm, frame, positions, hidden, as_of = inputs
    thin = pm[~pm.player_id.isin(hidden)]
    no_xa = frame.copy()
    no_xa.loc[no_xa.player_id.isin(hidden), "xa"] = np.nan
    on = _fit(thin, no_xa, positions, as_of=as_of, fpl_xg_fallback=True)
    assert (on.loc[hidden, "xg_source"] == "fpl").all()
    assert (on.loc[hidden, "xa_source"] == "prior").all()


def test_fails_closed_without_team_exposure(inputs):
    pm, frame, positions, hidden, as_of = inputs
    thin = pm[~pm.player_id.isin(hidden)].assign(team_xg=np.nan)
    off = _fit(thin, frame, positions)
    on = _fit(thin, frame, positions, as_of=as_of, fpl_xg_fallback=True)
    pd.testing.assert_frame_equal(on[SHARED[1:]], off[SHARED[1:]])
