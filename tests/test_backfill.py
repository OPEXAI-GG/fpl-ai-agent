"""The 2017/18 backfill, the vaastav history loader and FotMob inside the weekly update: resumable,
idempotent, and a broken source/season is skipped and reported instead of stopping the run."""

import pytest

from fpl_agent import notify
from fpl_agent.db import ingest_vaastav
from fpl_agent.db.database import FootballDB
from fpl_agent.pipeline import (
    OK,
    BackfillOptions,
    UpdateOptions,
    run_backfill,
    run_update,
)
from tests.helpers import clients, fotmob_client, make_http


@pytest.fixture(autouse=True)
def alerts(monkeypatch):
    sent = []
    monkeypatch.setattr(notify, "send", lambda msg, *a, **k: sent.append(msg) or True)
    return sent


def backfill(db=None, overrides=None, all_matches=True, **kw):
    db = db or FootballDB()
    _, us, http = clients(overrides)
    fm = fotmob_client(overrides, all_matches)
    opts = BackfillOptions(**{"start": 2024, "end": 2026, "competitions": ("ENG1", "ENG2"), **kw})
    return db, run_backfill(db, opts, fm=fm, us=us, http=http)


def names(res):
    return {r.name: r.ok for r in res.report.results}


# ---- vaastav loader ------------------------------------------------------------------------
def test_vaastav_loads_history_with_prices_and_double_gameweek_rows():
    db = FootballDB()
    text = make_http().get(ingest_vaastav.url(2024)).text
    n = ingest_vaastav.load_csv(db, text, 2024)
    assert n > 200
    pts = db.query("SELECT * FROM fpl_points_gw WHERE season = 2024")
    assert pts.player_id.notna().all()
    assert (pts.groupby(["gw", "fpl_id"]).size() > 1).any()  # the added second fixture
    snaps = db.query("SELECT * FROM fpl_players_gw WHERE season = 2024")
    assert snaps.now_cost.between(35, 150).all()
    assert set(snaps.position) <= {"GKP", "DEF", "MID", "FWD"}


def test_vaastav_links_a_player_to_an_existing_canonical_id():
    db = FootballDB()
    text = make_http().get(ingest_vaastav.url(2024)).text
    ingest_vaastav.load_csv(db, text, 2024)
    first = db.query("SELECT name FROM players WHERE name LIKE '% %' LIMIT 1").name[0]
    before = db.counts()["players"]
    ingest_vaastav.load_csv(db, text, 2025)  # next season, same people
    assert db.query("SELECT count(*) n FROM players WHERE name = ?", [first]).n[0] == 1
    assert db.counts()["players"] == before


def test_vaastav_old_seasons_get_positions_from_players_raw():
    db = FootballDB()
    res = backfill(db=db, sources=("vaastav",), start=2017, end=2018, competitions=("ENG1",))[1]
    assert names(res)["vaastav:2017"]
    pos = db.query("SELECT DISTINCT position FROM fpl_players_gw WHERE season = 2017").position
    assert set(pos) <= {"GKP", "DEF", "MID", "FWD"} and len(pos) >= 3


def test_vaastav_contract():
    assert ingest_vaastav.problems("<html>x</html>")
    assert ingest_vaastav.problems("a,b\n1,2\n")
    assert ingest_vaastav.problems(make_http().get(ingest_vaastav.url(2024)).text) == []
    assert ingest_vaastav.url(2017).endswith("data/2017-18/gws/merged_gw.csv")


# ---- backfill --------------------------------------------------------------------------------
def test_backfill_fills_every_source_for_every_season():
    db, res = backfill()
    c = db.counts()
    assert c["matches"] > 0 and c["fpl_points_gw"] > 0 and c["appearances"] > 0
    assert c["shots"] > 0 and c["match_odds"] > 0
    ok = names(res)
    assert ok["fotmob:ENG2:2026"] and ok["vaastav:2024"] and ok["football-data:E1:2026"]
    assert ok["understat:EPL:2025"]
    assert res.exit_code == OK, res.message()


def test_backfill_is_resumable_and_idempotent():
    db, first = backfill()
    counts = {k: v for k, v in db.counts().items() if k != "ingest_log"}
    _, second = backfill(db=db)
    assert {k: v for k, v in db.counts().items() if k != "ingest_log"} == counts
    # finished past seasons are remembered and not fetched again
    assert "vaastav:2024" not in names(second)
    assert "vaastav:2024" in names(first)


def test_fotmob_match_pages_are_capped_per_run_and_continue_next_run():
    db, _ = backfill(sources=("fotmob",), competitions=("ENG2",), start=2026, fotmob_matches=2)
    done = db.query("SELECT count(*) n FROM ingest_state WHERE source = 'fotmob'").n[0]
    assert done <= 2
    _, _ = backfill(db=db, sources=("fotmob",), competitions=("ENG2",), start=2026,
                    fotmob_matches=5)  # fmt: skip
    assert db.query("SELECT count(*) n FROM ingest_state WHERE source = 'fotmob'").n[0] >= done


def test_missing_match_page_is_retried_not_marked_done():
    db, res = backfill(sources=("fotmob",), competitions=("ENG2",), start=2026, all_matches=False)
    # only one recorded match page exists; the other finished matches 404 and stay pending
    done = db.query("SELECT key FROM ingest_state WHERE source = 'fotmob'").key.tolist()
    assert done == ["5836754"]
    assert not names(res)["fotmob:ENG2:2026"]  # >25% failed: flagged, not silent


def test_one_broken_source_does_not_stop_the_rest(alerts):
    db, res = backfill(overrides={"raw.githubusercontent.com": (503, "")})
    ok = names(res)
    assert not ok["vaastav:2024"] and ok["football-data:E1:2026"] and "fotmob:ENG2:2026" in ok
    assert db.counts()["matches"] > 0
    assert alerts and "backfill" in alerts[0]


def test_changed_fotmob_format_is_skipped_and_nothing_written():
    db, res = backfill(
        sources=("fotmob",), competitions=("ENG2",), start=2026,
        overrides={"leagues": (200, {"details": {}})},
    )  # fmt: skip
    assert not names(res)["fotmob:ENG2:2026"]
    assert db.counts()["matches"] == 0


def test_unknown_competition_is_reported_not_fatal():
    db, res = backfill(competitions=("XXX9",), sources=("fotmob",))
    assert not any(names(res).values())


# ---- FotMob inside the weekly update -----------------------------------------------------------
def test_weekly_update_with_fotmob(tmp_path):
    db = FootballDB()
    fpl, us, http = clients()
    opts = UpdateOptions(season=2026, football_data=("E1",), fotmob=("ENG2", "ENG1"),
                         fotmob_matches=10, export=tmp_path / "x")  # fmt: skip
    res = run_update(db, opts, fpl=fpl, us=us, http=http, fm=fotmob_client(all_matches=True),
                     alert=False)  # fmt: skip
    ok = names(res)
    assert ok["fotmob:ENG2"] and ok["fotmob:ENG1"]
    assert db.query("SELECT count(*) n FROM team_match WHERE source = 'fotmob'").n[0] >= 2
    assert (tmp_path / "x" / "appearances.parquet").exists()


def test_weekly_update_skips_a_dead_fotmob_but_keeps_everything_else(alerts):
    db = FootballDB()
    fpl, us, http = clients({"fotmob.com": (503, "")})
    opts = UpdateOptions(season=2026, football_data=("E1",), fotmob=("ENG2",))
    res = run_update(
        db, opts, fpl=fpl, us=us, http=http, fm=fotmob_client({"fotmob.com": (503, "")})
    )
    assert not names(res)["fotmob:ENG2"] and names(res)["fpl:players"]
    assert res.exit_code == OK
    assert alerts
