"""Give every Premier League match its FPL gameweek.

FPL numbers gameweeks by its own calendar (postponements, doubles), not by league matchday, and
past seasons come from the vaastav archive with kickoff times but no link to the matches table.
Matching on the exact kickoff time (both are UTC) labels almost every match; the rest keep their
gameweek empty rather than a guess.
"""

from __future__ import annotations

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
