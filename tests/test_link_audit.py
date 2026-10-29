"""The link audit measures how much FPL playing time has FotMob detail joined, per season, says
why each unlinked player is unlinked, and never writes; ingestion now records a FotMob player
whose name matches nobody."""

import hashlib
import json

import pandas as pd

from fpl_agent.cli import main
from fpl_agent.db.database import FootballDB
from fpl_agent.db.link_audit import coverage


def points(db, season, pid, minutes, fixture=1, gw=1):
    db.upsert("fpl_points_gw", pd.DataFrame([dict(
        season=season, gw=gw, fpl_id=pid, player_id=pid, fixture_id=fixture, minutes=minutes,
        total_points=2)]))  # fmt: skip


def league(db):
    """Four FPL players of 2026 (one also in 2025), one linked to FotMob by name."""
    linked = db.player_id("fpl-2026", 1, "Bukayo Saka", "MID")
    db.player_id("fotmob", 101, "Bukayo Saka", "MID", link_to=linked)
    pos = db.player_id("fpl-2026", 2, "Mohammed Kudus", "MID")
    db.name_link([pos], "Mohammed Kudus", "FWD", "fotmob")  # records the position reason
    db.player_id("fotmob", 102, "Mohammed Kudus", "FWD")
    nobody = db.player_id("fpl-2026", 3, "Levi Samuels Colwill", "DEF")
    old = db.player_id("fpl-2026", 4, "Jack Grealish", "MID")
    db.player_id("fotmob", 104, "Jack Grealish", "FWD")  # namesake, no reason kept
    points(db, 2026, linked, 90)
    points(db, 2026, pos, 60)
    points(db, 2026, nobody, 30)
    points(db, 2026, old, 20)
    points(db, 2026, old, 0, fixture=2, gw=2)  # did not play: no minutes, not a new player
    points(db, 2025, nobody, 90)
    return linked, pos, nobody, old


def test_counts_players_and_minutes_and_explains_every_unlinked_player():
    db = FootballDB()
    linked, pos, nobody, old = league(db)
    r = coverage(db, 2026)
    assert (r["players_played"], r["players_linked"], r["minutes"], r["minutes_linked"]) == (
        4,
        1,
        200,
        90,
    )
    assert r["player_share"] == 0.25 and r["minute_share"] == 0.45
    why = {u["player_id"]: u["reason"] for u in r["unlinked"]}
    assert why == {pos: "position FWD differs from MID", nobody: "no FotMob player with this name",
                   old: "unrecorded"}  # fmt: skip
    assert r["unlinked_reasons"] == {"position differs": 1, "no FotMob player with this name": 1,
                                     "unrecorded": 1}  # fmt: skip
    assert [u["player_id"] for u in r["unlinked"]] == [pos, nobody, old]  # most minutes first
    assert len(coverage(db, 2026, limit=1)["unlinked"]) == 1


def test_season_scoping_and_empty_seasons():
    db = FootballDB()
    league(db)
    r = coverage(db, 2025)
    assert (r["players_played"], r["minutes"], r["minute_share"]) == (1, 90, 0.0)
    empty = coverage(db, 2019)
    assert empty["players_played"] == 0 and empty["player_share"] is None
    assert empty["minute_share"] is None and empty["unlinked"] == []


def test_ingestion_records_a_name_that_matches_nobody():
    db = FootballDB()
    assert db.name_link([], "Levi Colwill", "DEF", "fotmob") is None
    why = db.query("SELECT key, detail FROM ingest_state WHERE source = 'link'")
    assert why.to_dict("records") == [{"key": "fotmob:levi colwill", "detail": "no name match"}]


def test_report_never_writes_and_reads_an_older_file(tmp_path, capsys):
    path = tmp_path / "old.duckdb"
    db = FootballDB(path)
    league(db)
    for t in ("player_links", "source_attempts", "fpl_deadlines", "player_codes"):
        db.con.execute(f"DROP TABLE IF EXISTS {t}")
    db.con.execute("CHECKPOINT")
    db.close()
    before = hashlib.sha256(path.read_bytes()).hexdigest()
    assert main(["db-links", "--db", str(path), "--season", "2026"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["players_linked"] == 1 and out["linked_by_evidence"] == 0
    assert hashlib.sha256(path.read_bytes()).hexdigest() == before
