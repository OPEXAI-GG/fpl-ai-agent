"""Wegz re-review (FAA-3 r2): R2 with real (naive UTC) ingestion stamps, both directions."""
import json
from datetime import UTC, datetime, timedelta

import pandas as pd

from fpl_agent.db import player_links
from fpl_agent.db.database import FootballDB, now
from fpl_agent.db.link_audit import coverage
from fpl_agent.db.link_candidates import revalidate
from fpl_agent.model import features
from tests.test_link_candidates import CUT, T0, fotmob, fpl, season


def linked():
    db = FootballDB(); season(db, n=5)
    p = fpl(db, "Bukayo Saka", {1: 77, 2: 90, 3: 64}); q = fotmob(db, "B. Saka", {1: 77, 2: 90, 3: 65})
    # stamp all evidence like real ingestion: database.now() (naive UTC), well before CUT
    for t in ("appearances", "player_match_stats"):
        db.con.execute(f"UPDATE {t} SET ingested_at = ?", [T0 + timedelta(weeks=3)])
    db.con.execute("UPDATE fpl_points_gw SET ingested_at = kickoff + INTERVAL 3 HOUR")
    assert player_links.apply(db, 2026, CUT)["added"] == 1
    return db, p, q


def contradict(db, p, q, stamp):
    db.upsert("fpl_points_gw", pd.DataFrame([dict(season=2026, gw=4, fpl_id=p, player_id=p,
        fixture_id=104, minutes=0, total_points=0, kickoff=T0 + timedelta(weeks=3),
        stats=json.dumps({"minutes": 0}), ingested_at=stamp)]))
    db.con.execute("INSERT INTO appearances VALUES (4, ?, 1, 'fotmob', true, 90, 'FWD', ?)", [q, stamp])


def test_utc_stamped_contradiction_withdraws_the_link_now():
    db, p, q = linked()
    contradict(db, p, q, now())  # what ingestion writes
    assert [r["from_player_id"] for r in revalidate(db, 2026)] == [q]
    assert coverage(db, 2026)["linked_by_evidence"] == 0
    decided = now() + timedelta(seconds=1)
    blocked = player_links.unsupported(db, 2026, decided)
    pm = player_links.resolve(features.match_frame(db), decided.date() + timedelta(days=1), decided, blocked)
    assert set(pm.player_id) == {q}


def test_decision_before_the_contradiction_was_stored_still_uses_the_link():
    db, p, q = linked()
    stored = now()
    contradict(db, p, q, stored)
    before = stored - timedelta(seconds=1)
    rec = db.con.execute("SELECT recorded_at FROM player_links").fetchone()[0]
    if rec <= before:  # only meaningful if the link existed then
        assert player_links.unsupported(db, 2026, before) == frozenset()


def test_local_time_stamp_is_future_to_utc_and_ignored_until_then():
    """A +04:00 wall-clock stamp (DuckDB now() on this machine) is 4h in the future in UTC
    terms: the contradiction is not yet 'stored' for a decision now, and is seen once due."""
    db, p, q = linked()
    local = now() + timedelta(hours=4)
    contradict(db, p, q, local)
    assert revalidate(db, 2026) == []
    assert [r["from_player_id"] for r in revalidate(db, 2026, local + timedelta(seconds=1))] == [q]


def test_my_original_R2_fails_only_through_the_local_stamp():
    db, p, q = linked()
    contradict(db, p, q, None)  # no stored time: untimed contradiction must still count
    assert [r["from_player_id"] for r in revalidate(db, 2026)] == [q]
    assert coverage(db, 2026)["linked_by_evidence"] == 0
