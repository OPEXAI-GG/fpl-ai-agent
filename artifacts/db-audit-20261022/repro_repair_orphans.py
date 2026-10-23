"""DB02 reproducer on an in-memory FootballDB: the repair/quarantine paths in
src/fpl_agent/db/quality.py enumerate match dependents by hand (MATCH_TABLES, MATCH_CHILDREN)
and omit match_odds_log, so deleting or merging a match orphans its logged prices.
Run from the repo root; nothing outside memory is touched."""

from datetime import date

from fpl_agent.db import quality
from fpl_agent.db.database import FootballDB, now


def odds_row(db, mid):
    db.con.execute(
        "INSERT INTO match_odds_log VALUES (?, 'x', 'avg', false, 2.0, 3.3, 3.8, 1.9, 1.9, "
        "NULL, NULL, NULL, ?)",
        [mid, now()],
    )


def orphans(db):
    return db.con.execute(
        "SELECT count(*) FROM match_odds_log l WHERE NOT EXISTS "
        "(SELECT 1 FROM matches m WHERE m.match_id = l.match_id)"
    ).fetchone()[0]


# 1. quarantine of a match 'vs itself' deletes the match but not its odds log
db = FootballDB(":memory:")
t = db.team_id("src", 1, "Alpha FC")
m = db.match_id("src", 10, "ENG1", 2026, date(2026, 8, 21), t, t, finished=False)
odds_row(db, m)
quality.quarantine(db)
print(
    "quarantine: matches left",
    db.con.execute("SELECT count(*) FROM matches").fetchone()[0],
    "| orphaned match_odds_log rows:",
    orphans(db),
)

# 2. merging a pairing stored twice (two sources) deletes the duplicate match
db = FootballDB(":memory:")
h = db.team_id("src", 1, "Alpha FC")
a = db.team_id("src", 2, "Beta FC")
keep = db.match_id("s1", 1, "ENG1", 2026, date(2026, 8, 21), h, a)
dup = db.con.execute("SELECT coalesce(max(match_id),0)+1 FROM matches").fetchone()[0]
db.con.execute(
    "INSERT INTO matches (match_id, competition_id, season, match_date, home_team_id, "
    "away_team_id, source, ingested_at) VALUES (?, 'ENG1', 2026, ?, ?, ?, 's2', ?)",
    [dup, date(2026, 8, 21), h, a, now()],
)
odds_row(db, dup)
n = quality._merge_duplicate_matches(db)
print(
    "merge: merged",
    n,
    "| matches left",
    db.con.execute("SELECT count(*) FROM matches").fetchone()[0],
    "| orphaned match_odds_log rows:",
    orphans(db),
    "(keep id",
    keep,
    "dup id",
    dup,
    ")",
)
