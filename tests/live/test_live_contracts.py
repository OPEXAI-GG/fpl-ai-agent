"""Canary: hits the REAL sources and checks they still look the way our parsers expect.
Excluded from normal CI (marker `live`); run daily by .github/workflows/canary.yml, whose
failure is the early warning that a source changed or went down."""

import httpx
import pytest

from fpl_agent import contracts
from fpl_agent.data import leagues
from fpl_agent.data.fotmob import FotMobClient
from fpl_agent.data.fpl_client import FPLClient
from fpl_agent.data.understat import UnderstatClient
from fpl_agent.db import ingest_vaastav
from fpl_agent.db.database import FootballDB
from fpl_agent.db.ingest_football_data import url
from fpl_agent.db.ingest_fotmob import FotMobIngestor
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


@pytest.mark.parametrize("league_id", [47, 48, 87, 42])
def test_fotmob_league_pages(league_id):
    raw = FotMobClient().league(league_id)
    assert contracts.fotmob_league(raw) == []


def test_fotmob_match_page_and_ingest():
    fm = FotMobClient()
    league = fm.league(48, "2025/2026")
    done = next(m for m in league["fixtures"]["allMatches"] if m["status"].get("finished"))
    page = fm.match(done["id"])
    assert contracts.fotmob_match(page) == []
    db = FootballDB()
    assert FotMobIngestor(db).load_match(page, leagues.get("ENG2"), 2025) >= 22


def test_fotmob_has_2017_history():
    raw = FotMobClient().league(48, "2017/2018")
    assert contracts.fotmob_league(raw) == []
    assert len(raw["fixtures"]["allMatches"]) > 500


def test_vaastav_history_file():
    r = httpx.get(ingest_vaastav.url(2017), follow_redirects=True, timeout=60)
    assert r.status_code == 200 and ingest_vaastav.problems(r.text) == []


def test_full_update_into_a_temp_database():
    db = FootballDB()
    opts = UpdateOptions(season=SEASON, points=True, players=True, player_limit=5,
                         fotmob=("ENG2",), fotmob_matches=5)  # fmt: skip
    res = run_update(db, opts, alert=False)
    assert res.exit_code == OK, res.message()
    assert not res.report.failed, res.message()
    assert errors(res.issues) == [], format_issues(res.issues)
