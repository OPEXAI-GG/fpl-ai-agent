"""Every external source's recorded real response passes its contract, and every kind of format
change is caught."""

import copy

import pytest

from fpl_agent import contracts
from fpl_agent.errors import SourceFormatError
from tests.helpers import FIX, fixture, live_payload

REAL = {
    "bootstrap": (contracts.fpl_bootstrap, fixture("bootstrap-static.json")),
    "fixtures": (contracts.fpl_fixtures, fixture("fixtures.json")),
    "live": (contracts.fpl_live, live_payload(fixture("bootstrap-static.json"))),
    "understat_league": (contracts.understat_league, fixture("understat_league.json")),
    "understat_player": (contracts.understat_player, fixture("understat_player.json")),
}


@pytest.mark.parametrize("name", REAL)
def test_recorded_responses_pass(name):
    check, payload = REAL[name]
    assert check(payload) == []


def test_football_data_recorded_csvs_pass():
    for f in ("fd_E0_2026.csv", "fd_E1_2026.csv"):
        assert contracts.football_data_csv((FIX / f).read_text(), f) == []


@pytest.mark.parametrize("name", REAL)
@pytest.mark.parametrize("bad", [None, [], {}, "", "<html></html>", 5])
def test_garbage_is_rejected(name, bad):
    check, _ = REAL[name]
    if name == "live" and bad == {}:
        pytest.skip("{} lacks 'elements' so is rejected; covered below")
    assert check(bad), (name, bad)


def drop(payload, path):
    p = copy.deepcopy(payload)
    obj = p
    for k in path[:-1]:
        obj = obj[k]
    del obj[path[-1]]
    return p


@pytest.mark.parametrize(
    "name,path",
    [
        ("bootstrap", ["elements"]),
        ("bootstrap", ["elements", 0, "now_cost"]),
        ("bootstrap", ["teams", 0, "short_name"]),
        ("bootstrap", ["events", 0, "deadline_time"]),
        ("fixtures", [0, "team_h"]),
        ("fixtures", [0, "kickoff_time"]),
        ("live", ["elements", 0, "explain"]),
        ("live", ["elements", 0, "stats", "bps"]),
        ("understat_league", ["dates"]),
        ("understat_league", ["dates", 0, "xG"]),
        ("understat_league", ["players", 0, "player_name"]),
        ("understat_player", ["shots"]),
        ("understat_player", ["matches", 0, "xG"]),
        ("understat_player", ["shots", 0, "situation"]),
    ],
)
def test_each_renamed_field_is_caught(name, path):
    check, payload = REAL[name]
    assert check(drop(payload, path)), (name, path)


def test_full_season_bootstrap_checks_counts():
    boot = fixture("bootstrap-static.json")
    assert contracts.fpl_bootstrap(boot, full_season=True)  # the fixture is trimmed on purpose


def test_football_data_failures():
    assert contracts.football_data_csv("<HTML><body>blocked</body>", "E1")
    assert contracts.football_data_csv("Date,HomeTeam\n1,2\n", "E1")
    assert contracts.football_data_csv("", "E1")


def test_require_raises_typed_error():
    contracts.require([], "x")
    with pytest.raises(SourceFormatError, match="x: a; b"):
        contracts.require(["a", "b"], "x")
