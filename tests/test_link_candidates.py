"""Appearance agreement proposes an FPL <-> FotMob identity only on canonical fixture and team
evidence, in both directions uniquely, with nothing contradicting it, and only from evidence
observed before the cutoff. The generator never writes."""

import hashlib
import json
import zlib
from datetime import datetime, timedelta

import pandas as pd

from fpl_agent.db import link_candidates as lc
from fpl_agent.db.database import FootballDB

A, B = 1, 2  # canonical teams
T0 = datetime(2026, 8, 22, 15)  # first kickoff; one match a week
SNAP = datetime(2026, 8, 20, 12)  # squad snapshot before the season
CUT = datetime(2026, 10, 1)
STORED = timedelta(hours=3)  # each match's rows are stored three hours after kickoff


def kick(m):
    return T0 + timedelta(weeks=m - 1)


def season(db, n=5):
    for t, name in ((A, "Alpha"), (B, "Beta")):
        db.con.execute("INSERT INTO teams VALUES (?, ?, ?)", [t, name, name.lower()])
    for i in range(1, n + 1):
        ko = T0 + timedelta(weeks=i - 1)
        home, away = (A, B) if i % 2 else (B, A)
        db.con.execute("INSERT INTO matches VALUES (?, 'ENG1', 2026, ?, ?, ?, ?, ?, 1, 0, true, "
                       "'fpl', now())", [i, i, ko, ko.date(), home, away])  # fmt: skip
        db.con.execute("INSERT INTO match_ids VALUES ('fpl-2026', ?, ?)", [str(100 + i), i])


def fpl(db, name, rows, team=A, snap=SNAP, fpl_id=None, observed=True, stored=None):
    """An FPL player: rows = {match: minutes or (minutes, {stat: value})}. `observed=False`
    makes the snapshot a reconstruction; `stored` {match: time} overrides when rows were stored."""
    pid = db.player_id("fpl-2026", fpl_id or zlib.crc32(name.encode()) % 10_000, name, "MID")
    db.upsert("fpl_players_gw", pd.DataFrame([dict(
        season=2026, gw=1, snapshot_at=snap, fpl_id=fpl_id or pid, player_id=pid, team_id=team,
        position="MID", raw=json.dumps({"element_type": 3} if observed else {"was_home": 1}))]))  # fmt: skip
    for m, v in rows.items():
        minutes, extra = v if isinstance(v, tuple) else (v, {})
        stats = {"minutes": minutes, "starts": int(minutes >= 60), "goals_scored": 0,
                 "yellow_cards": 0, "red_cards": 0, "saves": 0, **extra}  # fmt: skip
        db.upsert("fpl_points_gw", pd.DataFrame([dict(
            season=2026, gw=m, fpl_id=fpl_id or pid, player_id=pid, fixture_id=100 + m,
            minutes=minutes, total_points=1, stats=json.dumps(stats), kickoff=kick(m),
            ingested_at=(stored or {}).get(m, kick(m) + STORED))]))  # fmt: skip
    return pid


def fotmob(db, name, rows, team=A, sid=None, stored=None):
    """A FotMob-only record: rows = {match: minutes or (minutes, {stat: value})}."""
    qid = db.player_id("fotmob", sid or name, name, "FWD")
    for m, v in rows.items():
        minutes, extra = v if isinstance(v, tuple) else (v, {})
        at = (stored or {}).get(m, kick(m) + STORED)
        db.con.execute("INSERT INTO appearances VALUES (?, ?, ?, 'fotmob', ?, ?, 'FWD', ?)",
                       [m, qid, team, minutes >= 60, minutes, at])  # fmt: skip
        for stat, value in {"goals": 0, "yellow_cards": 0, **extra}.items():
            db.con.execute("INSERT INTO player_match_stats VALUES (?, ?, 'fotmob', ?, ?, ?)",
                           [m, qid, stat, value, at])  # fmt: skip
    return qid


def run(db, cutoff=CUT):
    r = lc.propose(lc.load(db, 2026, cutoff), cutoff)
    return ({a["fpl_player_id"]: a["candidate"] for a in r["accepted"]},
            {x["fpl_player_id"]: x["reason"] for x in r["rejected"]})  # fmt: skip


def test_a_consistent_record_is_proposed_with_its_evidence():
    db = FootballDB()
    season(db)
    p = fpl(db, "Bukayo Saka", {1: 77, 2: 90, 3: 64, 4: 0})
    q = fotmob(db, "B. Saka", {1: 77, 2: 90, 3: 65})
    acc, rej = run(db)
    assert acc == {p: q} and rej == {}
    rows = lc.evidence(db, 2026, CUT, p, q)
    assert [(r["m"], r["minutes_fpl"], r["minutes_fotmob"]) for r in rows] == [
        (1, 77, 77), (2, 90, 90), (3, 64, 65)]  # fmt: skip


def test_full_match_teammates_are_ambiguous_not_linked():
    db = FootballDB()
    season(db)
    p = fpl(db, "Centre Back", {1: 90, 2: 90, 3: 90})
    fotmob(db, "CB One", {1: 90, 2: 90, 3: 90})
    fotmob(db, "Keeper Two", {1: 90, 2: 90, 3: 90})
    acc, rej = run(db)
    assert acc == {} and rej[p].startswith("ambiguous: another FotMob record")


def test_substitutes_on_together_with_equal_minutes_are_ambiguous():
    db = FootballDB()
    season(db)
    p = fpl(db, "Late Sub", {1: 12, 3: 20})
    fotmob(db, "Sub One", {1: 12, 3: 20})
    fotmob(db, "Sub Two", {1: 12, 3: 21})
    assert run(db) == ({}, {p: "ambiguous: another FotMob record also fits"})


def test_a_player_of_the_other_side_with_equal_minutes_is_not_evidence():
    db = FootballDB()
    season(db)
    p = fpl(db, "Home Player", {1: 70, 2: 80, 3: 66})
    fotmob(db, "Away Player", {1: 70, 2: 80, 3: 66}, team=B)
    assert run(db) == ({}, {p: "no agreeing FotMob record"})


def test_one_appearance_is_never_enough():
    db = FootballDB()
    season(db)
    p = fpl(db, "Debutant", {2: 33})
    fotmob(db, "Debut", {2: 33})
    assert run(db) == ({}, {p: "too few appearances"})


def test_a_record_that_fits_two_fpl_players_links_neither():
    db = FootballDB()
    season(db)
    p1 = fpl(db, "Twin One", {1: 25, 2: 30})
    p2 = fpl(db, "Twin Two", {1: 25, 2: 30, 3: 0})
    fotmob(db, "Twin", {1: 25, 2: 30})
    acc, rej = run(db)
    assert acc == {}
    assert rej == {p1: "ambiguous: the FotMob record also fits another FPL player",
                   p2: "ambiguous: the FotMob record also fits another FPL player"}  # fmt: skip


def test_duplicate_evidence_counts_once_and_differing_duplicates_are_refused():
    db = FootballDB()
    season(db)
    db.con.execute("INSERT INTO match_ids VALUES ('fpl', '101', 1)")  # a second label, same match
    once = fpl(db, "Counted Once", {1: 50})
    fotmob(db, "Once", {1: 50})
    assert run(db)[1][once] == "too few appearances"
    two = fpl(db, "Two Ids", {1: 61, 2: 70, 3: 80}, fpl_id=7001)
    db.con.execute("INSERT INTO player_ids VALUES ('fpl-2026', '7002', ?, 'Two Ids')", [two])
    db.upsert("fpl_points_gw", pd.DataFrame([dict(
        season=2026, gw=2, fpl_id=7002, player_id=two, fixture_id=102, minutes=10,
        total_points=1, stats=json.dumps({"minutes": 10}), kickoff=kick(2),
        ingested_at=kick(2) + STORED)]))  # fmt: skip
    fotmob(db, "Two", {1: 61, 2: 70, 3: 80})
    assert run(db)[1][two] == "contradictory FPL rows"


def test_contradicting_matches_reject_the_pair():
    db = FootballDB()
    season(db)
    scored = fpl(db, "Scorer", {1: 90, 2: (88, {"goals_scored": 1}), 3: 90, 4: 70})
    fotmob(db, "Not Scorer", {1: 90, 2: 88, 3: 90, 4: 70})
    benched = fpl(db, "Benched", {1: 45, 2: 40, 3: 0, 4: 30}, team=B)
    fotmob(db, "Played Instead", {1: 45, 2: 40, 3: 55, 4: 30}, team=B)
    _, rej = run(db)
    assert rej[scored] == rej[benched] == "contradicted by the evidence"


def test_a_record_belonging_to_another_identity_is_a_conflict_unless_explained():
    db = FootballDB()
    season(db)
    p = fpl(db, "New Name", {1: 15, 2: 18, 3: 22})
    other = fpl(db, "Someone Else", {1: 0, 2: 0, 3: 0, 4: 0})
    db.con.execute("INSERT INTO player_ids VALUES ('fotmob', 'x', ?, 'x')", [other])
    db.con.execute("INSERT INTO appearances SELECT m, ?, ?, 'fotmob', false, mn, 'MID', "
                   "TIMESTAMP '2026-09-20' FROM (VALUES (1, 15), (2, 18), (3, 22)) v(m, mn)",
                   [other, A])  # fmt: skip
    assert run(db)[1][p] == "conflict: the FotMob record already belongs to fpl-2026 identity"
    db2 = FootballDB()
    season(db2)
    p2 = fpl(db2, "Bench Guy", {1: 15, 2: 18, 3: 22})
    held = fpl(db2, "Old Link", {1: 15, 2: 18, 3: 22})  # its own FPL rows agree: theirs
    db2.con.execute("INSERT INTO player_ids VALUES ('fotmob', 'y', ?, 'y')", [held])
    db2.con.execute("INSERT INTO appearances SELECT m, ?, ?, 'fotmob', false, mn, 'MID', "
                    "TIMESTAMP '2026-09-20' FROM (VALUES (1, 15), (2, 18), (3, 22)) v(m, mn)",
                    [held, A])  # fmt: skip
    q = fotmob(db2, "Bench Guy FM", {1: 15, 2: 18, 3: 22})
    acc, _ = run(db2)
    assert acc == {p2: q}


def test_a_record_held_by_an_older_identity_is_a_conflict():
    db = FootballDB()
    season(db)
    p = fpl(db, "Returning", {1: 64, 2: 71, 3: 90})
    q = fotmob(db, "Returning FM", {1: 64, 2: 71, 3: 90})
    db.con.execute("INSERT INTO player_ids VALUES ('vaastav-2023', '9', ?, 'Returning')", [q])
    assert run(db)[1][p] == "conflict: the FotMob record already belongs to vaastav-2023 identity"


def test_evidence_after_the_cutoff_is_ignored_and_valid_from_follows_the_evidence():
    db = FootballDB()
    season(db)
    p = fpl(db, "Riser", {1: 64, 2: 71, 3: 90})
    q = fotmob(db, "Riser FM", {1: 64, 2: 71, 3: 90})
    after_two = T0 + timedelta(weeks=1, days=1)
    assert run(db, T0 + timedelta(days=1)) == ({}, {p: "too few appearances"})
    assert run(db, after_two)[0] == {p: q}
    h = lc.history(db, 2026, CUT)
    assert h["accepted"][0]["valid_from"] == after_two.replace(hour=0).isoformat()
    # a squad snapshot taken after the cutoff is not team evidence for it
    late = FootballDB()
    season(late)
    p2 = fpl(late, "Late Snap", {1: 64, 2: 71, 3: 90}, snap=datetime(2026, 10, 5))
    fotmob(late, "Late Snap FM", {1: 64, 2: 71, 3: 90})
    assert run(late) == ({}, {p2: "no team evidence before the cutoff"})


def test_the_generator_never_writes(tmp_path):
    path = tmp_path / "f.duckdb"
    db = FootballDB(path)
    season(db)
    fpl(db, "Bukayo Saka", {1: 77, 2: 90})
    fotmob(db, "B. Saka", {1: 77, 2: 90})
    db.con.execute("CHECKPOINT")
    db.close()
    before = hashlib.sha256(path.read_bytes()).hexdigest()
    ro = FootballDB(path, schema="read")
    assert len(lc.history(ro, 2026, CUT)["accepted"]) == 1
    ro.close()
    assert hashlib.sha256(path.read_bytes()).hexdigest() == before


def test_valid_from_waits_for_the_first_team_evidence():
    db = FootballDB()
    season(db)
    snap = T0 + timedelta(weeks=2, hours=5)  # first squad snapshot after the third match
    p = fpl(db, "Mid Season", {1: 64, 2: 71, 3: 90}, snap=snap)
    q = fotmob(db, "Mid Season FM", {1: 64, 2: 71, 3: 90})
    h = lc.history(db, 2026, CUT)
    assert [(a["fpl_player_id"], a["candidate"]) for a in h["accepted"]] == [(p, q)]
    assert h["accepted"][0]["valid_from"] == (snap + timedelta(days=1)).replace(hour=0).isoformat()


def test_evidence_counts_only_once_stored_and_missing_times_never_count():
    db = FootballDB()
    season(db)
    late = datetime(2026, 9, 25)  # matches 1-3 were backfilled long after they were played
    p = fpl(db, "Backfilled", {1: 64, 2: 71, 3: 90}, stored=dict.fromkeys((1, 2, 3), late))
    q = fotmob(db, "Backfilled FM", {1: 64, 2: 71, 3: 90})
    assert run(db, late) == ({}, {})  # nothing about the player was stored yet
    assert run(db, late + timedelta(hours=1))[0] == {p: q}
    assert (
        lc.history(db, 2026, CUT)["accepted"][0]["valid_from"] == datetime(2026, 9, 26).isoformat()
    )
    db.con.execute("UPDATE appearances SET ingested_at = NULL")
    assert run(db) == ({}, {p: "no agreeing FotMob record"})


def test_reconstructed_snapshots_are_not_team_evidence():
    db = FootballDB()
    season(db)
    p = fpl(db, "Archive Only", {1: 64, 2: 71, 3: 90}, observed=False)
    fotmob(db, "Archive FM", {1: 64, 2: 71, 3: 90})
    assert run(db) == ({}, {p: "no team evidence before the cutoff"})


def test_valid_from_is_the_start_of_the_last_unbroken_acceptance():
    db = FootballDB()
    season(db)
    p = fpl(db, "Wobbly", {1: 64, 2: 71, 3: 90, 4: 0, 5: 30})
    q = fotmob(db, "Wobbly FM", {1: 64, 2: 71, 3: 90, 5: 30})
    # a rival fits matches 2 and 3 (ambiguous after match 3) until it plays match 4, which the
    # player sat out: then it is no longer a rival and the pair is accepted again
    fotmob(db, "Rival", {2: 71, 3: 90, 4: 60})
    day_after = {m: (kick(m) + timedelta(days=1)).replace(hour=0) for m in range(1, 6)}
    assert run(db, day_after[2])[0] == {p: q}
    assert run(db, day_after[3]) == ({}, {p: "ambiguous: another FotMob record also fits"})
    assert run(db, day_after[4])[0] == {p: q}
    h = lc.history(db, 2026, CUT)
    assert [(a["fpl_player_id"], a["valid_from"]) for a in h["accepted"]] == [
        (p, day_after[4].isoformat())]  # fmt: skip


def test_a_record_covering_only_part_of_the_players_games_is_not_enough():
    db = FootballDB()
    season(db)
    p = fpl(db, "Regular", {1: 64, 2: 71, 3: 90, 4: 80, 5: 85})
    fotmob(db, "Part Timer", {1: 64, 2: 71, 3: 90})  # 3 of the player's 5 games
    assert run(db) == ({}, {p: "agreement below threshold"})


def test_a_record_that_played_while_the_player_sat_out_is_no_rival():
    db = FootballDB()
    season(db, n=7)
    games = {1: 64, 2: 71, 3: 90, 5: 30, 6: 45, 7: 50}
    p = fpl(db, "Steady", {**games, 4: 0})
    q = fotmob(db, "Steady FM", games)
    # fits five of the player's games, but played match 4, which the player sat out
    fotmob(db, "Lookalike", {2: 71, 3: 90, 4: 60, 5: 30, 6: 45, 7: 50})
    assert run(db, datetime(2026, 11, 1))[0] == {p: q}


def test_valid_from_waits_for_late_stored_fotmob_rows():
    db = FootballDB()
    season(db)
    late = datetime(2026, 9, 25, 9)
    p = fpl(db, "Late FM", {1: 64, 2: 71, 3: 90})
    fotmob(db, "Late FM rec", {1: 64, 2: 71, 3: 90}, stored=dict.fromkeys((1, 2, 3), late))
    h = lc.history(db, 2026, CUT)
    assert [(a["fpl_player_id"], a["valid_from"]) for a in h["accepted"]] == [
        (p, datetime(2026, 9, 26).isoformat())]  # fmt: skip
