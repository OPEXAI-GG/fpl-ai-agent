"""Give every Premier League match its FPL gameweek.

FPL numbers gameweeks by its own calendar (postponements, doubles), not by league matchday, and
past seasons come from the vaastav archive with kickoff times but no link to the matches table.
Matching on the exact kickoff time (both are UTC) labels almost every match; the rest keep their
gameweek empty rather than a guess.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from fpl_agent.db.database import FootballDB


def assign_gameweeks(db: FootballDB, comp: str = "ENG1") -> int:
    """Set matches.matchday from fpl_points_gw kickoffs. Returns the number of matches labelled."""
    db.con.execute(
        """
        UPDATE matches SET matchday = k.gw
        FROM (SELECT season, kickoff, min(gw) AS gw FROM fpl_points_gw
              WHERE kickoff IS NOT NULL GROUP BY season, kickoff) k
        WHERE matches.competition_id = ? AND matches.season = k.season
          AND matches.kickoff = k.kickoff AND matches.matchday IS NULL
        """,
        [comp],
    )
    return db.con.execute(
        "SELECT count(*) FROM matches WHERE competition_id = ? AND matchday IS NOT NULL", [comp]
    ).fetchone()[0]


def _utc(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).replace(tzinfo=None)


def log_deadlines(db: FootballDB, events: list[dict[str, Any]], season: int,
                  observed_at: datetime) -> int:  # fmt: skip
    """Store each gameweek's official deadline as observed now, only when it differs from the
    newest stored observation (a moved deadline is a new version). Returns rows added."""
    added = 0
    for ev in events:
        if not ev.get("deadline_time"):
            continue
        at = _utc(ev["deadline_time"])
        if official_deadline(db, season, int(ev["id"])) == at:
            continue
        db.con.execute("INSERT OR REPLACE INTO fpl_deadlines VALUES (?, ?, ?, ?)",
                       [season, int(ev["id"]), at, observed_at])  # fmt: skip
        added += 1
    return added


def official_deadline(db: FootballDB, season: int, gw: int,
                      as_of: datetime | None = None) -> datetime | None:  # fmt: skip
    """The official deadline as last observed at or before `as_of` (newest when None); None
    when it was never observed by then (callers fall back to an approximation and say so)."""
    row = db.con.execute(
        "SELECT deadline_time FROM fpl_deadlines WHERE season = ? AND gw = ? "
        "AND (CAST(? AS TIMESTAMP) IS NULL OR observed_at <= ?) ORDER BY observed_at DESC LIMIT 1",
        [season, gw, as_of, as_of],
    ).fetchone()
    return row[0] if row else None
