"""Auditor reproductions (Wegz, FAA-3). Each test asserts the CURRENT (defective) behaviour."""
import json
from datetime import datetime, timedelta

import pandas as pd

from fpl_agent.db import link_candidates as lc, player_links
from fpl_agent.db.database import FootballDB
from fpl_agent.db.eligibility import excluded_fixtures
from fpl_agent.db.link_audit import coverage
from fpl_agent.db.quality import source_conflicts
from fpl_agent.model import features
from tests.test_link_candidates import CUT, SNAP, T0, fotmob, fpl, season


def linked(db):
    season(db, n=5)
    p = fpl(db, "Bukayo Saka", {1: 77, 2: 90, 3: 64})
    q = fotmob(db, "B. Saka", {1: 77, 2: 90, 3: 65})
    r = player_links.apply(db, 2026, CUT)
    assert r["added"] == 1
    return p, q, r


def test_R1_conflict_on_linked_pair_is_invisible_to_validated_exclusion():
    db = FootballDB()
    p, q, _ = linked(db)
    # match 4 arrives later: FPL says 90 min + 1 goal, FotMob record says 20 min, 0 goals
    db.upsert("fpl_points_gw", pd.DataFrame([dict(season=2026, gw=4, fpl_id=p, player_id=p,
        fixture_id=104, minutes=90, total_points=6, kickoff=T0 + timedelta(weeks=3),
        stats=json.dumps({"minutes": 90, "starts": 1, "goals_scored": 1}))]))
    db.con.execute("INSERT INTO appearances VALUES (4, ?, 1, 'fotmob', false, 20, 'FWD', now())", [q])
    db.con.execute("INSERT INTO player_match_stats VALUES (4, ?, 'fotmob', 'goals', 0, now())", [q])
    assert source_conflicts(db) == []            # conflict not detected
    assert excluded_fixtures(db) == (set(), set())  # so validated runs keep match 4
    pm = player_links.resolve(features.match_frame(db), datetime(2026, 12, 1))
    assert ((pm.match_id == 4) & (pm.player_id == p)).any()  # contradicted row credited to p


def test_R2_link_stays_active_after_contradicting_evidence():
    db = FootballDB()
    p, q, _ = linked(db)
    # later evidence: record plays for the player's side in a match FPL says p sat out
    db.upsert("fpl_points_gw", pd.DataFrame([dict(season=2026, gw=4, fpl_id=p, player_id=p,
        fixture_id=104, minutes=0, total_points=0, kickoff=T0 + timedelta(weeks=3),
        stats=json.dumps({"minutes": 0}))]))
    db.con.execute("INSERT INTO appearances VALUES (4, ?, 1, 'fotmob', true, 90, 'FWD', now())", [q])
    later = datetime(2026, 12, 1)
    # without the link, the rule would now reject the pair ...
    db2 = FootballDB(); season(db2, n=5)
    p2 = fpl(db2, "Bukayo Saka", {1: 77, 2: 90, 3: 64, 4: 0}); q2 = fotmob(db2, "B. Saka", {1: 77, 2: 90, 3: 65, 4: 90})
    r2 = lc.propose(lc.load(db2, 2026, later), later)
    assert not r2["accepted"] and "contradicted" in r2["rejected"][0]["reason"]
    # ... but with the link applied, p is silently skipped, nothing flags it, link stays active
    r = lc.propose(lc.load(db, 2026, later), later)
    assert p not in {x["fpl_player_id"] for x in r["accepted"] + r["rejected"]}
    assert coverage(db, 2026)["linked_by_evidence"] == 1
    assert player_links.batches(db)[0]["active"] == 1


def test_R3_link_recorded_later_is_used_by_an_earlier_cutoff():
    """valid_from is derived from kickoff dates only; recorded_at (and ingested_at of the
    evidence) are ignored, so a replay at a cutoff before the link (or its evidence) existed
    still sees it."""
    db = FootballDB()
    p, q, _ = linked(db)
    vf, rec = db.con.execute("SELECT valid_from, recorded_at FROM player_links").fetchone()
    early = vf + timedelta(days=1)
    assert early < rec  # the cutoff precedes the moment the link was recorded
    pm = player_links.resolve(features.match_frame(db), early)
    assert set(pm.player_id) == {p}


def test_R4_reconstructed_snapshot_is_accepted_as_team_evidence():
    db = FootballDB(); season(db, n=5)
    # snapshot rows with no element_type in raw = reconstructed (vaastav) by features.OBSERVED_SNAPSHOT
    p = fpl(db, "Bukayo Saka", {1: 77, 2: 90, 3: 64}, snap=T0)
    q = fotmob(db, "B. Saka", {1: 77, 2: 90, 3: 65})
    assert db.con.execute(f"SELECT count(*) FROM fpl_players_gw WHERE {features.OBSERVED_SNAPSHOT}").fetchone()[0] == 0
    r = lc.history(db, 2026, CUT)
    assert [(a["fpl_player_id"], a["candidate"]) for a in r["accepted"]] == [(p, q)]


def test_R5_redo_in_same_second_overwrites_the_undone_rows(monkeypatch):
    stamp = datetime(2026, 10, 26, 12, 0, 0)
    monkeypatch.setattr(player_links, "now", lambda: stamp)
    db = FootballDB(); season(db, n=5)
    fpl(db, "Bukayo Saka", {1: 77, 2: 90, 3: 64}); fotmob(db, "B. Saka", {1: 77, 2: 90, 3: 65})
    a = player_links.apply(db, 2026, CUT)
    assert player_links.undo(db, a["batch_id"]) == 1
    b = player_links.apply(db, 2026, CUT)
    assert a["batch_id"] == b["batch_id"]
    # the rollback record is gone: one row, active, no trace of the undo
    assert db.con.execute("SELECT count(*), count(rolled_back_at) FROM player_links").fetchone() == (1, 0)


def test_R6_player_links_outside_reference_registry():
    from fpl_agent.db.quality import REFERENCES
    cols = {c for refs in REFERENCES.values() for t, c in refs if t == "player_links"}
    assert cols == set()


def test_R7_a_2026_link_counts_as_coverage_for_another_season():
    db = FootballDB()
    p, q, _ = linked(db)
    db.upsert("fpl_points_gw", pd.DataFrame([dict(season=2025, gw=1, fpl_id=p, player_id=p,
        fixture_id=1, minutes=90, total_points=2, kickoff=datetime(2025, 8, 16),
        stats=json.dumps({"minutes": 90}))]))
    # q has no 2025 FotMob rows and the link is season 2026, yet 2025 reports p as linked
    assert not db.con.execute("SELECT count(*) FROM appearances a JOIN matches m USING (match_id) WHERE m.season=2025").fetchone()[0]
    c = coverage(db, 2025)
    assert c["linked_by_evidence"] == 1 and c["minute_share"] == 1.0
    # and the season-less match_frame join credits q's rows of any season to p
    sql = open("src/fpl_agent/model/features.py", encoding="utf-8").read()
    assert "l.season" not in sql
