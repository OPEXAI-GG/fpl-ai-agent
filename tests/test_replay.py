import pytest

from fpl_agent.model import replay
from fpl_agent.rules import Rules
from fpl_agent.solver.planner import WeekPlan
from tests.synth import build_world


@pytest.fixture(scope="module")
def world():
    return build_world(n_teams=10, seasons=(2023, 2024), seed=7)


@pytest.fixture(scope="module")
def result(world):
    cfg = replay.ReplayConfig(horizon=2, samples=80)
    return replay.replay_season(world.db, 2024, list(range(3, 9)), cfg)


def test_every_gameweek_is_replayed_and_scored(result):
    assert [w.gw for w in result.weeks] == list(range(3, 9))
    assert result.total == sum(w.points for w in result.weeks) > 0
    assert all(w.hits >= 0 and w.captain_points >= 0 for w in result.weeks)
    assert result.weeks[0].transfers == 0  # the first squad is a free pick, not transfers
    assert "points" in result.summary() and len(result.frame()) == 6


def test_chips_are_played_at_most_once_per_window(result):
    played = [w.chip for w in result.weeks if w.chip]
    assert len(played) == len(set(played))


def test_without_chips_none_is_played(world):
    cfg = replay.ReplayConfig(horizon=2, samples=60, use_chips=False)
    res = replay.replay_season(world.db, 2024, [3, 4, 5], cfg)
    assert not any(w.chip for w in res.weeks)


def _week(**kw):
    base = dict(gw=1, squad=list(range(1, 16)), xi=list(range(1, 12)), bench=[12, 13, 14, 15],
                captain=2, vice=3, transfers_in=[], transfers_out=[], hits=0, chip=None,
                ft_before=1, xp=0.0)  # fmt: skip
    return WeekPlan(**{**base, **kw})


POS = {1: "GKP", **{i: "DEF" for i in range(2, 6)}, **{i: "MID" for i in range(6, 10)},
       10: "FWD", 11: "FWD", 12: "DEF", 13: "MID", 14: "FWD", 15: "GKP"}  # fmt: skip


def test_score_week_subs_in_the_bench_and_covers_the_captain():
    rules = Rules.nearest(2025)
    pts = {i: 2 for i in range(1, 16)} | {2: 0, 3: 10, 12: 6}
    mins = {i: 90 for i in range(1, 16)} | {2: 0}  # the captain does not play
    total, cap, bench = replay.score_week(_week(), pts, mins, POS, rules)
    # 10 starters who played (2 each, 10 for the vice) + bench DEF 6 + the vice doubled
    assert cap == 10 and total == 9 * 2 + 10 + 6 + 10
    assert bench == 2 * 3  # MID, FWD, GK left on the bench


def test_score_week_bench_boost_and_triple_captain():
    rules = Rules.nearest(2025)
    pts = {i: 1 for i in range(1, 16)}
    mins = {i: 90 for i in range(1, 16)}
    assert replay.score_week(_week(chip="bboost"), pts, mins, POS, rules) == (16, 1, 0)
    assert replay.score_week(_week(chip="3xc"), pts, mins, POS, rules) == (13, 1, 4)


def test_settings_override_the_planner_and_chip_thresholds():
    cfg = replay.tuned(replay.ReplayConfig(), ["hit_buffer=1.5", "ft_values=2.2,1.5",
                       "min_gain.bboost=12", "hold_margin=2", "max_hits_per_gw=2", "horizon=6"])  # fmt: skip
    assert cfg.planner.hit_buffer == 1.5 and cfg.planner.ft_values == (2.2, 1.5)
    assert cfg.planner.max_hits_per_gw == 2 and cfg.horizon == 6
    assert cfg.chips.min_gain["bboost"] == 12 and cfg.chips.min_gain["wildcard"] == 6.0
    assert cfg.chips.hold_margin == 2.0
    with pytest.raises(ValueError, match="unknown setting"):
        replay.tuned(replay.ReplayConfig(), ["nonsense=1"])


def test_a_captain_booked_without_minutes_stays_in_but_the_vice_is_captain():
    """2026/27 rules: a card without minutes counts as playing for automatic substitution, but
    the armband passes to the vice-captain when the captain "plays no minutes"."""
    rules = Rules.nearest(2025)
    pts = {i: 2 for i in range(1, 16)} | {2: -1, 3: 10, 12: 6}  # captain booked on the bench
    mins = {i: 90 for i in range(1, 16)} | {2: 0}
    total, cap, bench = replay.score_week(_week(), pts, mins, POS, rules, cards={2: 1})
    # no substitution (the booked captain took part), the vice's 10 doubled
    assert cap == 10 and total == 9 * 2 - 1 + 10 + 10
    assert bench == 6 + 2 * 3
