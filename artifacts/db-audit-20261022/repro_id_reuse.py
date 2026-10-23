"""DB02 reproducer on an in-memory FootballDB (no file touched): FPL ids are reassigned each
season but id tables key on (source, source_id) only. Run from the repo root."""

from datetime import date

from fpl_agent.db.database import FootballDB

db = FootballDB(":memory:")
# season 2026: element 5 is "Alpha One", team 18 is "Ipswich", fixture 1 ended 2-0
a = db.player_id("fpl", 5, "Alpha One", "FWD")
t26 = db.team_id("fpl", 18, "Ipswich Town")
t_opp = db.team_id("fpl", 1, "Arsenal")
m26 = db.match_id(
    "fpl", 1, "ENG1", 2026, date(2026, 8, 21), t26, t_opp, home_goals=2, away_goals=0, finished=True
)
# season 2027: FPL reassigns element 5 to a different person, team 18 to a promoted club,
# and restarts fixture ids at 1
b = db.player_id("fpl", 5, "Beta Two", "DEF")
t27 = db.team_id("fpl", 18, "Burnley")
m27 = db.match_id(
    "fpl", 1, "ENG1", 2027, date(2027, 8, 14), t27, t_opp, home_goals=0, away_goals=1, finished=True
)
print(
    "player reused:",
    a == b,
    "| name stored:",
    db.con.execute("SELECT name FROM players WHERE player_id=?", [a]).fetchone()[0],
)
print(
    "team reused:",
    t26 == t27,
    "| name stored:",
    db.con.execute("SELECT name FROM teams WHERE team_id=?", [t26]).fetchone()[0],
)
print(
    "match reused:",
    m26 == m27,
    "| season/score now:",
    db.con.execute(
        "SELECT season, home_goals, away_goals, home_team_id=? FROM matches WHERE match_id=?",
        [t26, m26],
    ).fetchone(),
)
print(
    "matches rows:",
    db.con.execute("SELECT count(*) FROM matches").fetchone()[0],
    "(expected 2 if ids were season-scoped)",
)
