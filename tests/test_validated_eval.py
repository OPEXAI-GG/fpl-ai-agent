"""A validated evaluation sees only what existed at each decision cutoff, in every input it
fits, not only in the frame it hands the projection; and FPL fixture ids, reused every season,
are matched to conflicts and exclusions within their own season."""

import json
from datetime import timedelta

import pandas as pd
import pytest

from fpl_agent.db import eligibility
from fpl_agent.db.database import FootballDB
from fpl_agent.db.quality import source_conflicts
from fpl_agent.model import backtest, minutes, project, replay
from fpl_agent.model.simulate import SimConfig
from tests.synth import build_world

GW = 10
FLIP = {"GKP": "FWD", "FWD": "GKP", "DEF": "MID", "MID": "DEF"}


# ---- reused FPL fixture ids ------------------------------------------------------------------
def _fixture(db, season, mid, fpl_goals, fotmob_goals, labels=None, fpl_id=1):
    db.upsert("fpl_points_gw", pd.DataFrame([dict(
        season=season, gw=1, fpl_id=fpl_id, player_id=1, fixture_id=11, minutes=90,
        stats=json.dumps({"goals_scored": fpl_goals}))]))  # fmt: skip
    db.con.execute("INSERT INTO matches (match_id, season) VALUES (?, ?)", [mid, season])
    for label in labels or [f"fpl-{season}"]:
        db.con.execute("INSERT INTO match_ids VALUES (?, '11', ?)", [label, mid])
    db.con.execute("INSERT INTO player_match_stats VALUES (?, 1, 'fotmob', 'goals', ?, NULL)",
                   [mid, fotmob_goals])  # fmt: skip


def test_a_reused_fixture_id_agreeing_within_each_season_is_no_conflict():
    """Codex r4 probe: fixture 11 in 2025 and 2026, each season's FPL goals equal its own
    FotMob match; the bare numeric join reported two cross-season conflicts."""
    db = FootballDB()
    _fixture(db, 2025, 40, 0, 0)
    _fixture(db, 2026, 50, 1, 1)
    assert source_conflicts(db) == []
    assert eligibility.excluded_fixtures(db) == (set(), set())


@pytest.mark.parametrize("bad", [2025, 2026])
def test_a_genuine_conflict_excludes_only_its_own_season(bad):
    db = FootballDB()
    _fixture(db, 2025, 40, 0, 3 if bad == 2025 else 0)
    _fixture(db, 2026, 50, 1, 3 if bad == 2026 else 1)
    mid = {2025: 40, 2026: 50}[bad]
    found = source_conflicts(db)
    assert [(c["match_id"], c["season"], c["kinds"]) for c in found] == [(mid, bad, ["goals"])]
    assert source_conflicts(db, 2025 if bad == 2026 else 2026) == []
    assert eligibility.excluded_fixtures(db) == ({mid}, {(bad, 11)})
    # the same fixture id in the other season keeps its rows through validated filtering
    frame = pd.DataFrame({"season": [2025, 2026], "fixture_id": [11, 11]})
    kept = frame[[k not in {(bad, 11)} for k in zip(frame.season, frame.fixture_id,
                                                    strict=True)]]  # fmt: skip
    assert kept["season"].tolist() == [2026 if bad == 2025 else 2025]


def test_both_aliases_of_one_match_count_once():
    """A legacy `fpl` label and the season's own label on the same match must not double the
    FPL side (1 goal summed twice would read as 2 and conflict with FotMob's 1)."""
    db = FootballDB()
    _fixture(db, 2026, 50, 1, 1, labels=["fpl", "fpl-2026"])
    assert source_conflicts(db) == []
    db.con.execute("UPDATE player_match_stats SET value = 2")
    found = source_conflicts(db)
    assert len(found) == 1 and found[0]["fpl"]["goals"] == 1
    assert eligibility.excluded_fixtures(db) == ({50}, {(2026, 11)})


def test_a_label_is_bound_to_its_match_season():
    """A legacy label proven for a 2025 match, or a season label naming a match of another
    season, is not evidence about the 2026 fixture with the same id."""
    db = FootballDB()
    _fixture(db, 2025, 40, 0, 5, labels=["fpl"])  # genuine 2025 conflict via legacy label
    db.upsert("fpl_points_gw", pd.DataFrame([dict(
        season=2026, gw=1, fpl_id=2, player_id=1, fixture_id=11, minutes=90,
        stats=json.dumps({"goals_scored": 0}))]))  # fmt: skip
    db.con.execute("INSERT INTO match_ids VALUES ('fpl-2026', '11', 40)")  # mislabelled
    assert [(c["season"], c["match_id"]) for c in source_conflicts(db)] == [(2025, 40)]
    assert eligibility.excluded_fixtures(db)[1] == {(2025, 11)}


# ---- decision-time inputs of validated evaluations ----------------------------------------------
@pytest.fixture
def world():
    """Synthetic 2023-24 with 2024 GW10 made eligible: deadline recorded beforehand and an
    observed snapshot of every player three hours before it."""
    db = build_world(n_teams=6, seasons=(2023, 2024), seed=7).db
    ko = db.con.execute("SELECT min(kickoff) FROM fpl_points_gw WHERE season = 2024 AND gw = ?",
                        [GW]).fetchone()[0]  # fmt: skip
    cut = ko - timedelta(minutes=90)
    db.con.execute("INSERT INTO fpl_deadlines VALUES (2024, ?, ?, ?)",
                   [GW, cut, cut - timedelta(days=2)])  # fmt: skip
    db.con.execute("UPDATE fpl_players_gw SET raw = ?, snapshot_at = ? WHERE season = 2024 "
                   "AND gw = ?", [json.dumps({"element_type": 2}), cut - timedelta(hours=3), GW])  # fmt: skip
    assert eligibility.gameweek(db, 2024, GW)["class"] == "eligible"
    yield db, cut
    db.close()


class Stop(Exception):
    pass


def _late_history_snapshot(db, cut, gw=4):
    """A post-cutoff correction of a historical gameweek: every position flipped, prices up."""
    snap = db.query("SELECT * FROM fpl_players_gw WHERE season = 2024 AND gw = ?", [gw])
    db.upsert("fpl_players_gw", snap.assign(snapshot_at=cut + timedelta(hours=2),
                                            position=snap.position.map(FLIP),
                                            now_cost=snap.now_cost + 9))  # fmt: skip


ENTRYPOINTS = {
    "backtest": lambda db, gws, **k: backtest.projection_backtest(
        db, 2024, gws, SimConfig(n=40), **k
    ),
    "replay": lambda db, gws, **k: replay.replay_season(
        db, 2024, gws, replay.ReplayConfig(samples=40, horizon=1), **k
    ),
}


@pytest.mark.parametrize("entry", sorted(ENTRYPOINTS))
def test_the_fitted_history_ignores_later_snapshots_and_late_fixtures(world, entry, monkeypatch):
    """Codex r4 probe on the actual entrypoints: the history MinutesModel is fitted on must not
    change when a later correction of a past gameweek arrives after the cutoff, and a postponed
    fixture played after the cutoff is not in it."""
    db, cut = world
    seen = []

    def capture(cls, history):
        seen.append(history.copy())
        raise Stop

    monkeypatch.setattr(minutes.MinutesModel, "fit", classmethod(capture))
    run = ENTRYPOINTS[entry]
    with pytest.raises(Stop):
        run(db, [GW], validated=True)
    _late_history_snapshot(db, cut)
    fixture = db.con.execute("SELECT min(fixture_id) FROM fpl_points_gw WHERE season = 2024 AND "
                             "gw = 5").fetchone()[0]  # fmt: skip
    db.con.execute("UPDATE fpl_points_gw SET kickoff = ? WHERE season = 2024 AND fixture_id = ?",
                   [cut + timedelta(days=1), fixture])  # fmt: skip
    with pytest.raises(Stop):
        run(db, [GW], validated=True)
    before, after = seen
    assert not (after["kickoff"] >= pd.Timestamp(cut)).any()
    assert not ((after["season"] == 2024) & (after["fixture_id"] == fixture)).any()
    gone = (before["season"] == 2024) & (before["fixture_id"] == fixture)
    assert gone.any()
    keep = before[~gone].reset_index(drop=True)
    cols = ["player_id", "season", "gw", "fixture_id", "position", "price", "team_id"]
    pd.testing.assert_frame_equal(keep[cols], after[cols].reset_index(drop=True))


def test_a_reused_model_was_fitted_at_its_own_earlier_cutoff(monkeypatch):
    """Refit reuse: one fit serves several gameweeks; it saw only what the first one's cutoff
    allowed, so a correction landing between two weeks' cutoffs never reaches it."""
    db = build_world(n_teams=6, seasons=(2023, 2024), seed=7).db
    first = db.con.execute("SELECT min(kickoff) FROM fpl_points_gw WHERE season = 2024 AND "
                           "gw = ?", [GW]).fetchone()[0]  # fmt: skip
    cut = project.approx_deadline(first)
    _late_history_snapshot(db, cut)  # after GW10's cutoff, before GW11's
    fits = []
    real = minutes.MinutesModel.fit.__func__

    def capture(cls, history):
        fits.append(history.copy())
        return real(cls, history)

    monkeypatch.setattr(minutes.MinutesModel, "fit", classmethod(capture))
    backtest.projection_backtest(db, 2024, [GW, GW + 1], SimConfig(n=40), refit_every=6)
    assert len(fits) == 1
    h = fits[0]
    gw4 = h[(h["season"] == 2024) & (h["gw"] == 4)]
    orig = db.query("SELECT player_id, position FROM fpl_players_gw WHERE season = 2024 AND "
                    "gw = 4 AND snapshot_at < ?", [cut]).set_index("player_id")["position"]  # fmt: skip
    assert (gw4["position"].to_numpy() == gw4["player_id"].map(orig).to_numpy()).all()
    db.close()


def _capture_projection(monkeypatch):
    seen = {}

    def capture(*a, **k):
        seen.update(k)
        raise Stop

    monkeypatch.setattr(project, "build_projection", capture)
    return seen


def test_a_validated_replay_pool_is_the_observed_pre_deadline_snapshot(world, monkeypatch):
    """A later target-week snapshot (new prices, an injury) does not reach the pool, and the
    availability FPL showed before the deadline is kept, not reset to available."""
    db, cut = world
    pid = db.con.execute("SELECT min(player_id) FROM fpl_players_gw WHERE season = 2024 AND "
                         "gw = ?", [GW]).fetchone()[0]  # fmt: skip
    db.con.execute("UPDATE fpl_players_gw SET status = 'd', chance_next = 50 WHERE season = 2024 "
                   "AND gw = ? AND player_id = ?", [GW, pid])  # fmt: skip
    snap = db.query("SELECT * FROM fpl_players_gw WHERE season = 2024 AND gw = ?", [GW])
    seen = _capture_projection(monkeypatch)
    with pytest.raises(Stop):
        ENTRYPOINTS["replay"](db, [GW], validated=True)
    before = seen["pool"].set_index("player_id").sort_index()
    db.upsert("fpl_players_gw", snap.assign(snapshot_at=cut + timedelta(hours=3),
                                            now_cost=snap.now_cost + 9, status="i"))  # fmt: skip
    with pytest.raises(Stop):
        ENTRYPOINTS["replay"](db, [GW], validated=True)
    after = seen["pool"].set_index("player_id").sort_index()
    pd.testing.assert_series_equal(before["price"], after["price"])
    assert after.loc[pid, "status"] == "d" and after.loc[pid, "chance"] == 50
    assert (after.drop(index=pid)["status"] == "a").all()
    # exploratory keeps its documented every-player-available pool
    with pytest.raises(Stop):
        ENTRYPOINTS["replay"](db, [GW])
    assert (seen["pool"]["status"] == "a").all() and seen["pool"]["chance"].isna().all()


@pytest.mark.parametrize("entry", sorted(ENTRYPOINTS))
def test_validated_evaluations_use_only_odds_known_at_the_cutoff(world, entry, monkeypatch):
    db, _ = world
    seen = _capture_projection(monkeypatch)
    with pytest.raises(Stop):
        ENTRYPOINTS[entry](db, [GW], validated=True)
    assert seen["known_at_odds"] is True
    with pytest.raises(Stop):
        ENTRYPOINTS[entry](db, [GW])
    assert seen["known_at_odds"] is False  # exploratory: the documented event-time fallback


def test_known_at_odds_never_use_a_price_of_unknown_observation_time():
    db = FootballDB()
    db.con.execute("INSERT INTO match_odds (match_id, source, bookmaker, home, draw, away, closing, "
                   "ingested_at) VALUES (1, 'x', 'b', 2.0, 3.4, 3.8, false, '2030-01-01')")  # fmt: skip
    cut = pd.Timestamp("2024-01-01").to_pydatetime()
    assert project.market_lambdas(db, 1, cut, known_at=True) is None
    assert project.market_lambdas(db, 1, cut, known_at=False) is not None
