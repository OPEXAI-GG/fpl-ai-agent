"""Data-quality checks on the football database: duplicates, orphan ids, impossible values and a
cross-check of stored FPL points against the rules engine. `error` findings mean the data must not
be used to make decisions; `warn` findings are reported."""

from __future__ import annotations

import json
from dataclasses import dataclass

from fpl_agent.db.database import FootballDB
from fpl_agent.rules import Rules, score

VALID_STATUS = {"a", "d", "i", "n", "s", "u"}
# competitions whose seasons have a fixed number of matches
EXPECTED_MATCHES = {"ENG1": 380, "ENG2": 552, "ENG3": 552}


MAX_DISAGREEMENT = 0.005  # share of finished matches whose sources may disagree


@dataclass(frozen=True)
class Issue:
    severity: str  # "error" | "warn"
    check: str
    detail: str


def _count(db: FootballDB, sql: str, params: list | None = None) -> int:
    return db.con.execute(sql, params or []).fetchone()[0]


def run_checks(db: FootballDB, rules: Rules | None = None) -> list[Issue]:
    issues: list[Issue] = []

    def check(severity: str, name: str, sql: str, what: str, params: list | None = None) -> None:
        n = _count(db, sql, params)
        if n:
            issues.append(Issue(severity, name, f"{n} {what}"))

    # --- duplicates ---------------------------------------------------------------------
    check("error", "duplicate_matches",
          "SELECT count(*) FROM (SELECT 1 FROM matches WHERE competition_id NOT IN "
          "(SELECT competition_id FROM competitions WHERE tier = 0) GROUP BY competition_id, "
          "season, home_team_id, away_team_id HAVING count(*) > 1)",
          "league pairings stored more than once")  # fmt: skip
    check("error", "duplicate_team_keys",
          "SELECT count(*) FROM (SELECT 1 FROM teams GROUP BY key HAVING count(*) > 1)",
          "club keys duplicated")  # fmt: skip
    check("warn", "duplicate_player_names",
          "SELECT count(*) FROM (SELECT 1 FROM players p JOIN player_ids i USING (player_id) "
          "WHERE i.source = 'fpl' GROUP BY lower(p.name) HAVING count(*) > 1)",
          "FPL players share a full name (check they are different people)")  # fmt: skip

    # --- orphans / broken ids ---------------------------------------------------------
    for table, col, parent, pcol in [
        ("match_ids", "match_id", "matches", "match_id"),
        ("team_ids", "team_id", "teams", "team_id"),
        ("player_ids", "player_id", "players", "player_id"),
        ("team_match", "match_id", "matches", "match_id"),
        ("appearances", "match_id", "matches", "match_id"),
        ("appearances", "player_id", "players", "player_id"),
        ("player_match_stats", "player_id", "players", "player_id"),
        ("matches", "home_team_id", "teams", "team_id"),
        ("matches", "away_team_id", "teams", "team_id"),
        ("fpl_players_gw", "player_id", "players", "player_id"),
        ("fpl_players_gw", "team_id", "teams", "team_id"),
    ]:
        check("error", f"orphan_{table}.{col}",
              f"SELECT count(*) FROM {table} t LEFT JOIN {parent} p ON t.{col} = p.{pcol} "
              f"WHERE t.{col} IS NOT NULL AND p.{pcol} IS NULL",
              f"{table}.{col} values with no {parent} row")  # fmt: skip
    check("error", "fpl_points_unlinked",
          "SELECT count(*) FROM fpl_points_gw WHERE player_id IS NULL",
          "official point rows not linked to a player")  # fmt: skip

    # --- impossible values ----------------------------------------------------------------
    check(
        "error",
        "same_team_match",
        "SELECT count(*) FROM matches WHERE home_team_id = away_team_id",
        "matches vs itself",
    )
    check("error", "finished_without_score",
          "SELECT count(*) FROM matches WHERE finished AND (home_goals IS NULL OR "
          "away_goals IS NULL)", "finished matches with no score")  # fmt: skip
    check("error", "negative_or_huge_goals",
          "SELECT count(*) FROM team_match WHERE goals_for < 0 OR goals_for > 15 OR "
          "goals_against < 0 OR goals_against > 15", "team_match rows with impossible goals")  # fmt: skip
    check("error", "bad_xg",
          "SELECT count(*) FROM team_match WHERE xg_for < 0 OR xg_for > 12 OR xg_against < 0 "
          "OR xg_against > 12", "team_match rows with impossible xG")  # fmt: skip
    check("error", "bad_minutes",
          "SELECT count(*) FROM appearances WHERE minutes < 0 OR minutes > 130",
          "appearances with impossible minutes")  # fmt: skip
    check("error", "bad_shots",
          "SELECT count(*) FROM shots WHERE xg < 0 OR xg > 1 OR x < 0 OR x > 1 OR y < 0 OR y > 1 "
          "OR minute < 0 OR minute > 130", "shots with impossible xG, location or minute")  # fmt: skip
    check("error", "bad_prices",
          "SELECT count(*) FROM fpl_players_gw WHERE now_cost < 30 OR now_cost > 250",
          "FPL price rows outside £3.0m-£25.0m")  # fmt: skip
    check("error", "bad_chance",
          "SELECT count(*) FROM fpl_players_gw WHERE chance_next < 0 OR chance_next > 100",
          "availability percentages outside 0-100")  # fmt: skip
    n_bad = _count(
        db,
        "SELECT count(*) FROM fpl_players_gw WHERE status NOT IN "
        f"({', '.join(repr(s) for s in sorted(VALID_STATUS))})",
    )
    if n_bad:
        issues.append(Issue("error", "bad_status", f"{n_bad} FPL rows with unknown status"))
    check("error", "odds_below_one",
          "SELECT count(*) FROM match_odds WHERE home < 1 OR draw < 1 OR away < 1",
          "odds rows with decimal odds below 1.0")  # fmt: skip
    check("warn", "xg_goals_gap",
          "SELECT count(*) FROM team_match WHERE xg_for IS NOT NULL AND abs(xg_for - goals_for) > 6",
          "team-matches where xG and goals differ by more than 6 (suspicious)")  # fmt: skip

    # --- completeness ------------------------------------------------------------------------
    for comp, expected in EXPECTED_MATCHES.items():
        n = _count(db, "SELECT count(*) FROM matches WHERE competition_id = ? AND season = "
                       "(SELECT max(season) FROM matches WHERE competition_id = ?)", [comp, comp])  # fmt: skip
        if n > expected:
            issues.append(Issue("error", "too_many_matches", f"{comp}: {n} matches > {expected}"))
    mismatched = _count(
        db,
        "SELECT count(*) FROM matches m JOIN team_match t ON t.match_id = m.match_id "
        "AND t.team_id = m.home_team_id WHERE m.finished AND t.goals_for <> m.home_goals",
    )
    if mismatched:
        finished = max(_count(db, "SELECT count(*) FROM matches WHERE finished"), 1)
        # a few disagreements are expected across sources (late corrections, awarded results);
        # a lot means a source or a team mapping is broken
        severity = "error" if mismatched / finished > MAX_DISAGREEMENT else "warn"
        issues.append(
            Issue(severity, "score_disagreement", f"{mismatched} matches where sources disagree")
        )

    # --- FPL points vs the rules engine ---------------------------------------------------
    issues += _points_consistency(db, rules or Rules.load(2026))
    return issues


def _points_consistency(db: FootballDB, rules: Rules) -> list[Issue]:
    """Recompute stored official points with the rules engine (single-fixture rows)."""
    rows = db.con.execute(
        "SELECT f.fpl_id, p.position, f.stats, f.total_points, f.gw FROM fpl_points_gw f "
        "JOIN (SELECT DISTINCT season, fpl_id, position FROM fpl_players_gw) p "
        "USING (season, fpl_id) WHERE f.season = ? AND f.fixture_id IN (SELECT fixture_id FROM fpl_points_gw GROUP BY fpl_id, gw, "
        "fixture_id HAVING count(*) = 1) AND f.fpl_id NOT IN (SELECT fpl_id FROM fpl_points_gw "
        "GROUP BY fpl_id, gw HAVING count(*) > 1)",
        [rules["season"]],
    ).fetchall()  # older seasons were scored under different rules
    bad = 0
    for _, position, stats, total, _gw in rows:
        if score(json.loads(stats), position, rules)["total"] != total:
            bad += 1
    if bad:
        return [Issue("error", "points_vs_rules", f"{bad} of {len(rows)} player-GWs disagree")]
    return []


def errors(issues: list[Issue]) -> list[Issue]:
    return [i for i in issues if i.severity == "error"]


def format_issues(issues: list[Issue]) -> str:
    return "\n".join(f"[{i.severity}] {i.check}: {i.detail}" for i in issues) or "no issues"


def quarantine(db: FootballDB) -> list[Issue]:
    """Remove individually impossible rows and report each removal as a warning.

    One bad shot or one mis-mapped match must not throw away a multi-hour load, and it must not
    be used either, so the rows are dropped and named (with enough context to find the cause).
    Anything systematic is still caught by `run_checks`, which runs after this.
    """
    out: list[Issue] = []
    with db.transaction():
        n = _count(
            db,
            "SELECT count(*) FROM shots WHERE xg < 0 OR xg > 1 OR x < 0 OR x > 1 OR y < 0 "
            "OR y > 1 OR minute < 0 OR minute > 130",
        )
        if n:
            db.con.execute(
                "DELETE FROM shots WHERE xg < 0 OR xg > 1 OR x < 0 OR x > 1 OR y < 0 OR y > 1 "
                "OR minute < 0 OR minute > 130"
            )
            out.append(
                Issue("warn", "quarantined_shots", f"dropped {n} shots with impossible values")
            )
        n = _count(db, "SELECT count(*) FROM fpl_players_gw WHERE now_cost < 30 OR now_cost > 250")
        if n:
            db.con.execute("DELETE FROM fpl_players_gw WHERE now_cost < 30 OR now_cost > 250")
            out.append(Issue("warn", "quarantined_prices",
                             f"dropped {n} FPL price rows outside the possible range"))  # fmt: skip
        own = db.query(
            "SELECT m.match_id, m.competition_id AS comp, m.season, m.match_date, t.name "
            "FROM matches m JOIN teams t ON t.team_id = m.home_team_id "
            "WHERE m.home_team_id = m.away_team_id"
        )
        for r in own.itertuples():
            out.append(Issue("warn", "quarantined_match",
                             f"{r.comp} {r.season} {r.match_date}: {r.name} vs itself "
                             "(two clubs mapped to one team); match dropped"))  # fmt: skip
        if len(own):
            ids = [int(i) for i in own["match_id"]]
            marks = ", ".join("?" for _ in ids)
            for table in (
                "team_match",
                "appearances",
                "player_match_stats",
                "shots",
                "match_odds",
                "match_ids",
                "matches",
            ):
                db.con.execute(f"DELETE FROM {table} WHERE match_id IN ({marks})", ids)
    return out
