"""Canary: hits the REAL sources and checks they still look the way our parsers expect.
Excluded from normal CI (marker `live`); run daily by .github/workflows/canary.yml, whose
failure is the early warning that a source changed or went down."""

import httpx
import pytest

from fpl_agent import contracts
from fpl_agent.data.fpl_client import FPLClient
from fpl_agent.data.understat import UnderstatClient
from fpl_agent.db.database import FootballDB
from fpl_agent.db.ingest_football_data import url
from fpl_agent.db.quality import errors, format_issues
from fpl_agent.pipeline import OK, UpdateOptions, run_update

pytestmark = pytest.mark.live
SEASON = 2026


def test_fpl_bootstrap_and_fixtures():
    c = FPLClient()
    assert contracts.fpl_bootstrap(c.bootstrap_raw(), full_season=True) == []
    fx = c.fixtures_raw()
    assert contracts.fpl_fixtures(fx) == [] and len(fx) == 380


def test_fpl_live_gw1():
    assert contracts.fpl_live(FPLClient().live_raw(1)) == []


def test_understat():
    u = UnderstatClient()
    league = u.league_data(str(SEASON))
    assert contracts.understat_league(league) == []
    pid = league["players"][0]["id"]
    assert contracts.understat_player(u.player_data(pid)) == []


@pytest.mark.parametrize("div", ["E0", "E1"])
def test_football_data(div):
    r = httpx.get(url(div, SEASON), follow_redirects=True, timeout=30)
    assert r.status_code == 200
    assert contracts.football_data_csv(r.text, div) == []


def test_full_update_into_a_temp_database():
    db = FootballDB()
    opts = UpdateOptions(season=SEASON, points=True, players=True, player_limit=5)
    res = run_update(db, opts, alert=False)
    assert res.exit_code == OK, res.message()
    assert not res.report.failed, res.message()
    assert errors(res.issues) == [], format_issues(res.issues)
