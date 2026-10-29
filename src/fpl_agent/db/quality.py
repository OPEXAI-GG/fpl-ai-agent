"""Data-quality checks on the football database: duplicates, orphan ids, impossible values and a
cross-check of stored FPL points against the rules engine. `error` findings mean the data must not
be used to make decisions; `warn` findings are reported."""

from __future__ import annotations

import json
from dataclasses import dataclass

import duckdb

from fpl_agent.data.leagues import REPEATING
from fpl_agent.db.database import DISTINCT_CLUBS, FootballDB, now, team_key
from fpl_agent.rules import Rules, score

VALID_STATUS = {"a", "d", "i", "n", "s", "u"}
# competitions whose seasons have a fixed number of matches
EXPECTED_MATCHES = {"ENG1": 380, "ENG2": 552, "ENG3": 552}


SIGNED_STATS = {"goals_prevented"}  # FotMob: saves above (or below) expectation
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
          "(SELECT competition_id FROM competitions WHERE tier = 0) AND competition_id NOT IN "
          f"({', '.join(repr(c) for c in sorted(REPEATING))}) GROUP BY competition_id, "
          "season, home_team_id, away_team_id HAVING count(*) > 1)",
          "league pairings stored more than once")  # fmt: skip
    check("error", "duplicate_team_keys",
          "SELECT count(*) FROM (SELECT 1 FROM teams GROUP BY key HAVING count(*) > 1)",
          "club keys duplicated")  # fmt: skip
    check("warn", "duplicate_player_names",
          "SELECT count(*) FROM (SELECT 1 FROM players p JOIN player_ids i USING (player_id) "
          "WHERE regexp_matches(i.source, '^fpl(-[0-9]+)?$') GROUP BY lower(p.name) "
          "HAVING count(DISTINCT p.player_id) > 1)",
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
        # model inputs (U06): every row a feature reads must reach its parent
        ("fpl_points_gw", "player_id", "players", "player_id"),
        ("player_match_stats", "match_id", "matches", "match_id"),
        ("shots", "match_id", "matches", "match_id"),
        ("match_odds", "match_id", "matches", "match_id"),
        ("match_odds_log", "match_id", "matches", "match_id"),
        ("appearances", "team_id", "teams", "team_id"),
        ("team_match", "team_id", "teams", "team_id"),
    ]:
        check("error", f"orphan_{table}.{col}",
              f"SELECT count(*) FROM {table} t LEFT JOIN {parent} p ON t.{col} = p.{pcol} "
              f"WHERE t.{col} IS NOT NULL AND p.{pcol} IS NULL",
              f"{table}.{col} values with no {parent} row")  # fmt: skip
    if "player_links" not in getattr(db, "missing_tables", []):
        for col in ("from_player_id", "to_player_id"):  # only active links are read
            check("error", f"orphan_player_links.{col}",
                  f"SELECT count(*) FROM player_links t LEFT JOIN players p ON t.{col} = "
                  f"p.player_id WHERE t.rolled_back_at IS NULL AND p.player_id IS NULL",
                  f"active player_links.{col} values with no players row")  # fmt: skip
    check("warn", "player_link_collision",
          "SELECT count(*) FROM (SELECT 1 FROM player_ids GROUP BY player_id, source "
          "HAVING count(*) > 1)",
          "players with two ids from the same source linked to them (a possible merge of two "
          "people; check the link)")  # fmt: skip
    check("warn", "dgw_history_legacy",
          "SELECT count(*) FROM (SELECT 1 FROM fpl_points_gw f WHERE f.explain <> '[]' AND "
          "NOT EXISTS (SELECT 1 FROM ingest_state s WHERE s.source = 'fpl_points' AND "
          "s.key = f.season || ':' || f.gw) GROUP BY season, gw, fpl_id "
          "HAVING count(*) > 1 AND count(DISTINCT stats) = 1)",
          "double-gameweek rows stored by older code with the GW totals copied to each fixture "
          "(reload with db-update --points --reload-gws on a backed-up database)")  # fmt: skip
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
          "SELECT count(*) FROM match_odds WHERE home < 1 OR draw < 1 OR away < 1 OR over25 < 1 "
          "OR under25 < 1 OR ah_home < 1 OR ah_away < 1",
          "odds rows with decimal odds below 1.0")  # fmt: skip
    check("error", "odds_log_below_one",
          "SELECT count(*) FROM match_odds_log WHERE home < 1 OR draw < 1 OR away < 1 OR "
          "over25 < 1 OR under25 < 1 OR ah_home < 1 OR ah_away < 1",
          "logged odds (the prices decisions are replayed with) below 1.0")  # fmt: skip

    # --- model-input contracts (U06): values and joins the projections rely on ---------------
    check("error", "bad_position",
          "SELECT count(*) FROM fpl_players_gw WHERE position NOT IN ('GKP', 'DEF', 'MID', 'FWD')",
          "FPL snapshot rows with an unknown position")  # fmt: skip
    check("error", "bad_fpl_minutes",
          "SELECT count(*) FROM fpl_points_gw WHERE minutes < 0 OR minutes > 130",
          "official point rows with impossible minutes")  # fmt: skip
    check("error", "fpl_points_without_kickoff",
          "SELECT count(*) FROM fpl_points_gw WHERE fixture_id <> 0 AND kickoff IS NULL",
          "official point rows of a real fixture with no kickoff (they would pass every "
          "decision cutoff)")  # fmt: skip
    check("error", "bad_bps",
          "SELECT count(*) FROM fpl_points_gw WHERE bps < -30 OR bps > 200",
          "official rows with an impossible bonus points system score")  # fmt: skip
    check("error", "bad_appearance_position",
          "SELECT count(*) FROM appearances WHERE position NOT IN ('GKP', 'DEF', 'MID', 'FWD')",
          "appearances with an unknown position")  # fmt: skip
    check("error", "finished_without_date",
          "SELECT count(*) FROM matches WHERE finished AND match_date IS NULL",
          "finished matches with no date (team ratings could not place them in time)")  # fmt: skip
    check("warn", "scheduled_without_kickoff",
          "SELECT count(*) FROM matches WHERE competition_id = 'ENG1' AND matchday IS NOT NULL "
          "AND kickoff IS NULL", "Premier League fixtures in a gameweek with no kickoff time "
          "(projected without per-player kickoff order)")  # fmt: skip
    check("error", "duplicate_player_fixture",
          "SELECT count(*) FROM (SELECT 1 FROM fpl_points_gw WHERE fixture_id <> 0 AND "
          "player_id IS NOT NULL GROUP BY season, gw, player_id, fixture_id HAVING count(*) > 1)",
          "player-fixtures with more than one official row (two FPL ids linked to one player: "
          "feature joins would count the match twice)")  # fmt: skip
    check("error", "duplicate_player_snapshot",
          "SELECT count(*) FROM (SELECT 1 FROM fpl_players_gw WHERE player_id IS NOT NULL "
          "GROUP BY season, gw, snapshot_at, player_id HAVING count(DISTINCT fpl_id) > 1)",
          "snapshots where one player has two FPL ids")  # fmt: skip
    check("error", "negative_match_stat",
          f"SELECT count(*) FROM player_match_stats WHERE value < 0 AND stat NOT IN "
          f"({', '.join(repr(x) for x in sorted(SIGNED_STATS))})",
          "per-match stats below zero (only goals prevented may be negative)")  # fmt: skip
    check("error", "bad_translation",
          "SELECT count(*) FROM league_translation WHERE factor IS NULL OR factor <= 0 OR "
          "NOT isfinite(factor)", "league translation factors that are not positive")  # fmt: skip
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
        issues.append(Issue(severity, "score_disagreement",
                            f"{mismatched} matches where sources disagree ({_where(db)})"))  # fmt: skip

    n_conf = len(source_conflicts(db))
    if n_conf:  # retained, not corrected: the stored evidence cannot say which source is right
        issues.append(Issue("warn", "fpl_fotmob_conflict",
                            f"{n_conf} player-matches where FPL and FotMob disagree on goals or "
                            "minutes (>5); excluded from validated evaluations (db-eligibility)"))  # fmt: skip

    if "player_links" not in getattr(db, "missing_tables", []):
        from fpl_agent.db.link_candidates import revalidate  # it builds on this module

        seasons = db.con.execute("SELECT DISTINCT season FROM player_links WHERE "
                                 "rolled_back_at IS NULL").fetchall()  # fmt: skip
        stale = [r for (ssn,) in seasons for r in revalidate(db, ssn)]
        if stale:
            issues.append(Issue("warn", "player_link_unsupported",
                                f"{len(stale)} active evidence links the stored evidence no longer "
                                "supports; projections leave them unlinked (db-links)"))  # fmt: skip

    # --- FPL points vs the rules engine ---------------------------------------------------
    issues += _points_consistency(db, rules or Rules.load(2026))
    return issues


MINUTES_TOLERANCE = 5  # FPL and FotMob count stoppage time differently

# (season, FPL fixture id, canonical match) for every FPL fixture label, bound to the season of
# the canonical match: FPL reuses fixture ids every season, so a bare numeric join would pair a
# fixture with another season's match. The legacy `fpl` label counts only for its match's own
# season, `fpl-<season>` only when the match is of that season; both aliases of one match
# collapse to one row (DISTINCT), so nothing is counted twice.
FPL_FIXTURE_MATCHES = (
    "(SELECT DISTINCT m.season, i.source_id, i.match_id FROM match_ids i JOIN matches m "
    "USING (match_id) WHERE i.source = 'fpl' OR i.source = 'fpl-' || m.season::VARCHAR)"
)


def source_conflicts(db: FootballDB, season: int | None = None) -> list[dict]:
    """Player-matches where FPL's official record and FotMob disagree on goals, or on minutes by
    more than MINUTES_TOLERANCE (F-16). Neither is corrected: the retained evidence cannot say
    which is right. Evaluations that claim validated inputs exclude these matches. A FotMob
    record joined to the FPL player by an active evidence link (`player_links`) is compared like
    one under the player's own id (`linked_record` names it), whenever the link was written: an
    exclusion only removes data, so a later-found conflict still excludes the match."""
    f = ("WITH f AS (SELECT i.match_id, x.season, x.player_id, "
         "sum(TRY_CAST(json_extract_string(x.stats, '$.goals_scored') AS DOUBLE)) AS goals, "
         f"sum(x.minutes) AS minutes FROM fpl_points_gw x JOIN {FPL_FIXTURE_MATCHES} i ON "
         "i.season = x.season AND i.source_id = x.fixture_id::VARCHAR "
         "WHERE x.player_id IS NOT NULL AND (? IS NULL OR x.season = ?) GROUP BY ALL) ")  # fmt: skip

    def compare(record: str, joins: str) -> str:
        """FPL rows against the FotMob rows of `record` (the player's own id, or a linked one)."""
        return (f"SELECT f.match_id, f.season, f.player_id, f.goals, s.value, f.minutes, "
                f"a.minutes, {record} FROM f {joins}"
                "LEFT JOIN player_match_stats s ON s.match_id = f.match_id "
                f"AND s.player_id = coalesce({record}, f.player_id) AND s.stat = 'goals' "
                "AND s.source = 'fotmob' LEFT JOIN appearances a ON a.match_id = f.match_id "
                f"AND a.player_id = coalesce({record}, f.player_id) AND a.source = 'fotmob' "
                "WHERE f.goals <> s.value OR abs(f.minutes - a.minutes) > ?")  # fmt: skip

    sql = f + compare("NULL::INTEGER", "")
    params = [season, season, MINUTES_TOLERANCE]
    if "player_links" not in getattr(db, "missing_tables", []):
        sql += " UNION ALL " + compare(
            "l.from_player_id", "JOIN player_links l ON l.to_player_id = f.player_id "
            "AND l.source = 'fotmob' AND l.rolled_back_at IS NULL ")  # fmt: skip
        params.append(MINUTES_TOLERANCE)
    rows = db.con.execute(sql + " ORDER BY ALL", params).fetchall()
    out = []
    for mid, ssn, pid, fg, sg, fm, am, record in rows:
        kinds = [k for k, bad in (("goals", sg is not None and fg != sg),
                                  ("minutes", am is not None and abs(fm - am) > MINUTES_TOLERANCE))
                 if bad]  # fmt: skip
        out.append({"match_id": mid, "season": ssn, "player_id": pid, "kinds": kinds,
                    "fpl": {"goals": fg, "minutes": fm}, "fotmob": {"goals": sg, "minutes": am},
                    "linked_record": record})  # fmt: skip
    return out


def _points_consistency(db: FootballDB, rules: Rules) -> list[Issue]:
    """Recompute stored official points with the rules engine (single-fixture rows). Only
    players with one known position in the season are scored: an unknown position is reported
    by `bad_position`, and a mid-season change would otherwise join (and score) a row twice."""
    rows = db.con.execute(
        "SELECT f.fpl_id, p.position, f.stats, f.total_points, f.gw FROM fpl_points_gw f "
        "JOIN (SELECT season, fpl_id, any_value(position) AS position FROM fpl_players_gw "
        "GROUP BY ALL HAVING count(DISTINCT position) = 1 AND bool_and(position IN "
        "('GKP', 'DEF', 'MID', 'FWD'))) p "
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


def _where(db: FootballDB) -> str:
    """Where the score disagreements are, so the cause can be found from the alert alone."""
    df = db.query(
        "SELECT m.competition_id AS comp, m.season, t.source, count(*) AS n, "
        "any_value(ht.name || ' ' || m.home_goals || '-' || m.away_goals || ' ' || aw.name || "
        "', ' || t.source || ' says ' || t.goals_for || '-' || t.goals_against) AS eg "
        "FROM matches m JOIN team_match t ON t.match_id = m.match_id "
        "AND t.team_id = m.home_team_id JOIN teams ht ON ht.team_id = m.home_team_id "
        "JOIN teams aw ON aw.team_id = m.away_team_id "
        "WHERE m.finished AND t.goals_for <> m.home_goals GROUP BY ALL ORDER BY n DESC LIMIT 5"
    )
    return "; ".join(f"{r.comp} {r.season} {r.source}: {r.n}, e.g. {r.eg}" for r in df.itertuples())


def format_issues(issues: list[Issue]) -> str:
    return "\n".join(f"[{i.severity}] {i.check}: {i.detail}" for i in issues) or "no issues"


# Every column that holds a canonical id, per kind of id. `test_quality` reconciles this with
# the declared schema, so a table added later cannot be missed by a merge or a delete.
REFERENCES: dict[str, tuple[tuple[str, str], ...]] = {
    "match": (("team_match", "match_id"), ("appearances", "match_id"),
              ("player_match_stats", "match_id"), ("shots", "match_id"),
              ("match_odds", "match_id"), ("match_odds_log", "match_id"),
              ("match_ids", "match_id"), ("matches", "match_id")),
    "team": (("team_ids", "team_id"), ("team_match", "team_id"), ("appearances", "team_id"),
             ("matches", "home_team_id"), ("matches", "away_team_id"),
             ("fpl_players_gw", "team_id"), ("transfers", "from_team_id"),
             ("transfers", "to_team_id"), ("teams", "team_id")),
    "player": (("player_ids", "player_id"), ("player_codes", "player_id"),
               ("appearances", "player_id"), ("player_match_stats", "player_id"),
               ("shots", "player_id"), ("transfers", "player_id"),
               ("fpl_players_gw", "player_id"), ("fpl_points_gw", "player_id"),
               ("projections", "player_id"), ("scout_cache", "player_id"),
               ("player_links", "from_player_id"), ("player_links", "to_player_id"),
               ("players", "player_id")),
}  # fmt: skip
# id-like columns that are deliberately not canonical references, and why
NOT_REFERENCES = {
    ("migration_log", "canonical_id"): "journal of the R1 relabel; restored by its own rollback",
    ("shots", "source_match_id"): "the source's own match id (text), not a canonical id",
}

# a match's rows, children first (the parent is deleted last)
MATCH_TABLES = tuple(t for t, _ in REFERENCES["match"])
REPAIR_KEY = "repeat-pairings-v1"


DONE_MARKERS = {"ingest_state": "source <> 'repair'",  # "this repair ran" rows, not data
                "repair_log": "false",  # the journal of a change, not a change
                "source_attempts": "false"}  # a record that a source was tried  # fmt: skip


def table_digests(db: FootballDB, skip_markers: bool = False) -> dict[str, tuple]:
    """Row count and an order-free content hash per table (a change detector, not a proof)."""
    names = [r[0] for r in db.con.execute(
        "SELECT table_name FROM duckdb_tables() WHERE database_name = current_database() "
        "AND schema_name = 'main' ORDER BY 1"
    ).fetchall()]  # fmt: skip
    where = DONE_MARKERS if skip_markers else {}
    return {
        n: db.con.execute(
            f"SELECT count(*), sum(hash(t))::VARCHAR FROM {n} t WHERE {where.get(n, 'true')}"
        ).fetchone()
        for n in names
    }


def blocked_writes(db: FootballDB, step, name: str) -> list[Issue]:
    """Run a repair/quarantine `step` on a throwaway in-memory copy instead of `db`. `db` is
    never written. If the step would change any table, that is an error: a destructive
    automatic fix is needed and must be reviewed (and backed up) before it runs for real. A
    step that would only record its own "done" marker changes no data and is not blocked. The
    error names, per table, how many rows the step would remove and write (its journal)."""
    orig = db.con.execute("SELECT current_database()").fetchone()[0]
    db.con.execute("ATTACH ':memory:' AS repair_probe")
    try:
        db.con.execute(f'COPY FROM DATABASE "{orig}" TO repair_probe')
        db.con.execute("USE repair_probe")
        before = table_digests(db, skip_markers=True)
        seen = [
            r[0] for r in db.con.execute("SELECT DISTINCT repair_id FROM repair_log").fetchall()
        ]
        found = step(db)
        after = table_digests(db, skip_markers=True)
        impact = [f"{t} {a} {n}" for t, a, n in db.con.execute(
            "SELECT table_name, action, count(*) FROM repair_log WHERE NOT list_contains(?, "
            "repair_id) GROUP BY ALL ORDER BY ALL", [seen]).fetchall()]  # fmt: skip
    finally:
        db.con.execute(f'USE "{orig}"')
        db.con.execute("DETACH repair_probe")
    changed = sorted(t for t in after if after[t] != before.get(t))
    if not changed:
        return []
    detail = f"{name} would change {', '.join(changed)}; blocked (repairs are opt-in: back up, then rerun with --repairs)"
    if impact:
        detail += f" [rows: {', '.join(impact)}]"
    reasons = "; ".join(i.detail for i in found)
    return [Issue("error", f"{name}_blocked", detail + (f": {reasons}" if reasons else ""))]


# ---- reversible changes (R2) -------------------------------------------------------------
class RepairConflict(Exception):
    """A rollback that would not restore exactly what a repair changed; nothing was written."""


class Journal:
    """Before and after images of every row one repair step removes or rewrites, kept in
    `repair_log` inside the step's own transaction, so the change and its record commit or fail
    together. `rollback_repair(db, journal.repair_id)` undoes the step exactly."""

    def __init__(self, db: FootballDB, step: str) -> None:
        self.db, self.step = db, step
        self.repair_id = f"{step}:{now():%Y%m%dT%H%M%S%f}"
        self.seq = 0

    def _keep(self, table: str, action: str, select: str, params: list, reason: str) -> int:
        n = self.db.con.execute(
            "INSERT INTO repair_log SELECT ?, ? + row_number() OVER (), ?, ?, ?, "
            f"{_image(self.db, table)}, ?, ?, NULL FROM ({select}) t",
            [self.repair_id, self.seq, self.step, table, action, reason, now(), *params],
        ).fetchone()[0]
        self.seq += n
        return n

    def delete(self, table: str, where: str, params: list, reason: str) -> int:
        n = self._keep(table, "removed", f"SELECT * FROM {table} WHERE {where}", params, reason)
        if n:
            self.db.con.execute(f"DELETE FROM {table} WHERE {where}", params)
        return n

    def move(self, table: str, col: str, old: int, new: int, reason: str) -> int:
        """Re-point every `col = old` reference at `new`."""
        n = self._keep(table, "removed", f"SELECT * FROM {table} WHERE {col} = ?", [old], reason)
        if n:
            self._keep(table, "added", f"SELECT * REPLACE (?::INTEGER AS {col}) FROM {table} "
                       f"WHERE {col} = ?", [new, old], reason)  # fmt: skip
            self.db.con.execute(f"UPDATE {table} SET {col} = ? WHERE {col} = ?", [new, old])
        return n

    def rewrite(self, table: str, where: str, params: list, run, reason: str) -> int:
        """Rows selected by `where` (a key the change leaves alone) before and after `run()`."""
        n = self._keep(table, "removed", f"SELECT * FROM {table} WHERE {where}", params, reason)
        run()
        self._keep(table, "added", f"SELECT * FROM {table} WHERE {where}", params, reason)
        return n


def _columns(db: FootballDB, table: str) -> list[tuple[str, str]]:
    return [(c[0], c[1]) for c in db.con.execute(f"DESCRIBE {table}").fetchall()]


def _image(db: FootballDB, table: str, alias: str = "t") -> str:
    """A row of `table` as JSON text. A JSON column is kept as its stored text, not re-encoded
    (re-encoding changes spacing and escapes, and the restored row would not be the same)."""
    fields = ", ".join(
        f"'{c}': {alias}.\"{c}\"" + ("::VARCHAR" if t == "JSON" else "")
        for c, t in _columns(db, table)
    )
    return f"to_json({{{fields}}})::VARCHAR"


def _struct(db: FootballDB, table: str) -> str:
    return json.dumps({c: "VARCHAR" if t == "JSON" else t for c, t in _columns(db, table)})


def rollback_repair(db: FootballDB, repair_id: str) -> dict[str, int]:
    """Undo one journalled repair step, newest change first, all or nothing. A row the step
    wrote must still be exactly as written, and a row it removed must fit back without a key
    collision; otherwise something changed it since, and RepairConflict is raised with
    nothing written. Returns {table: rows restored}. Repeating a completed rollback is a no-op
    that writes nothing and returns {}; an id the journal does not hold is a RepairConflict."""
    rows = db.con.execute(
        "SELECT seq, table_name, action, image FROM repair_log WHERE repair_id = ? "
        "AND rolled_back_at IS NULL ORDER BY seq DESC", [repair_id]).fetchall()  # fmt: skip
    if not rows:
        if db.con.execute("SELECT 1 FROM repair_log WHERE repair_id = ? LIMIT 1",
                          [repair_id]).fetchone():  # fmt: skip
            return {}  # already rolled back (rolled_back_at is set for the whole step at once)
        raise RepairConflict(f"{repair_id}: no journalled change to roll back")
    restored: dict[str, int] = {}
    with db.transaction():
        for seq, table, action, row in rows:
            if action == "added":
                gone = db.con.execute(
                    f"DELETE FROM {table} WHERE rowid = (SELECT min(rowid) FROM {table} t "
                    f"WHERE {_image(db, table)} = ?)", [row]).fetchone()[0]  # fmt: skip
                if not gone:
                    raise RepairConflict(f"{repair_id}: {table} row {seq} changed since the repair")
            else:
                try:
                    db.con.execute(f"INSERT INTO {table} BY NAME SELECT unnest(json_transform("
                                   "?::JSON, ?))", [row, _struct(db, table)])  # fmt: skip
                except duckdb.ConstraintException as e:
                    raise RepairConflict(f"{repair_id}: {table} row {seq} collides: {e}") from e
                restored[table] = restored.get(table, 0) + 1
        db.con.execute("UPDATE repair_log SET rolled_back_at = ? WHERE repair_id = ?",
                       [now(), repair_id])  # fmt: skip
    return restored


def repair(db: FootballDB) -> list[Issue]:
    """Fixes for data stored by older code, run before every load."""
    return _repair_once(db) + merge_split_teams(db)


def _repair_once(db: FootballDB) -> list[Issue]:
    """One-off fixes for data stored by older code (once per database).

    * Split-season leagues (Scotland, Belgium) were keyed by pairing alone, so their two
      meetings of the same home pairing were merged into one match. Their matches are removed
      and the FotMob pages marked unread, so the next loads store each meeting on its own.
    * Before Dundee and Dundee United were kept apart, one source's Dundee was linked to Dundee
      United; that link is removed and made again on the next load.
    """
    done = db.con.execute(
        "SELECT 1 FROM ingest_state WHERE source = 'repair' AND key = ?", [REPAIR_KEY]
    ).fetchone()
    if done:
        return []
    comps = sorted(lg for lg in REPEATING if not lg.startswith("U"))  # leagues, not the cups
    marks = ", ".join("?" for _ in comps)
    j = Journal(db, REPAIR_KEY)
    with db.transaction():
        ids = [r[0] for r in db.con.execute(
            f"SELECT match_id FROM matches WHERE competition_id IN ({marks})", comps
        ).fetchall()]  # fmt: skip
        if ids:
            id_marks = ", ".join("?" for _ in ids)
            for table in MATCH_TABLES:
                j.delete(table, f"match_id IN ({id_marks})", ids, "split-season pairing merged")
        for comp in comps:
            j.delete("ingest_state", "source = 'fotmob' AND detail LIKE ?", [f"{comp} %"],
                     "page of a removed split-season match: read again")  # fmt: skip
        stale = 0
        for source, sid, name, key in db.con.execute(
            "SELECT i.source, i.source_id, i.source_name, t.key FROM team_ids i "
            "JOIN teams t USING (team_id)"
        ).fetchall():
            if frozenset({team_key(name), key}) in DISTINCT_CLUBS:
                j.delete("team_ids", "source = ? AND source_id = ?", [source, sid],
                         f"{name} was linked to the club {key!r}")  # fmt: skip
                stale += 1
        j.rewrite("ingest_state", "source = 'repair' AND key = ?", [REPAIR_KEY], lambda: (
            db.con.execute(
                "INSERT OR REPLACE INTO ingest_state VALUES ('repair', ?, 'done', ?, now())",
                [REPAIR_KEY, f"{len(ids)} matches, {stale} team links"])), "done marker")  # fmt: skip
    if not ids and not stale:
        return []
    return [Issue("warn", "repaired", f"removed {len(ids)} {'/'.join(comps)} matches merged by "
                  f"pairing and {stale} wrong club links; they reload over the next runs "
                  f"(undo: {j.repair_id})")]  # fmt: skip


# every column that holds a canonical team id (except the teams row itself)
TEAM_COLUMNS = tuple(r for r in REFERENCES["team"] if r[0] != "teams")
# child tables of a match, with the primary-key columns a moved row could collide on (a table
# without a key, such as match_odds_log, is moved as it is)
MATCH_CHILDREN = {"team_match": ("team_id", "source"), "appearances": ("player_id", "source"),
                  "player_match_stats": ("player_id", "source", "stat"),
                  "match_odds": ("source", "bookmaker", "closing"),
                  "match_ids": ("source", "source_id")}  # fmt: skip


def merge_split_teams(db: FootballDB) -> list[Issue]:
    """Merge clubs stored twice because their names were matched before an alias existed
    ("Peterboro" from football-data and "Peterborough United" from FotMob), and then the matches
    that split stored twice (one per source). Runs on every load, so a new alias takes effect on
    data already stored without a one-off repair. Every change is journalled (`Journal`)."""
    groups: dict[str, list[tuple[int, str]]] = {}
    for tid, name in db.con.execute("SELECT team_id, name FROM teams ORDER BY team_id").fetchall():
        groups.setdefault(team_key(name), []).append((tid, name))
    split = {k: g for k, g in groups.items() if len(g) > 1}
    stale = [(k, g[0][0]) for k, g in groups.items() if len(g) == 1]
    out: list[Issue] = []
    j = Journal(db, "merge-split-teams")
    with db.transaction():
        for key, members in split.items():
            (keep, name), *dups = members
            for dup, dup_name in dups:
                _move_team(j, dup, keep)
                out.append(Issue("warn", "merged_teams",
                                 f"{dup_name} merged into {name} (same club, key {key!r})"))  # fmt: skip
            _set_key(j, keep, key)
        for key, tid in stale:  # keys stored under older aliases, so new names find the club
            _set_key(j, tid, key)
        if split:
            n = _merge_duplicate_matches(j)
            if n:
                out.append(Issue("warn", "merged_matches",
                                 f"{n} matches stored once per source were merged"))  # fmt: skip
    return out


def _set_key(j: Journal, tid: int, key: str) -> None:
    db = j.db
    if db.con.execute("SELECT 1 FROM teams WHERE team_id = ? AND key IS DISTINCT FROM ?",
                      [tid, key]).fetchone():  # fmt: skip
        j.rewrite("teams", "team_id = ?", [tid], lambda: db.con.execute(
            "UPDATE teams SET key = ? WHERE team_id = ?", [key, tid]), "club key alias")  # fmt: skip


def _move_team(j: Journal, dup: int, keep: int) -> None:
    j.delete("team_match", "team_id = ? AND EXISTS (SELECT 1 FROM team_match k WHERE "
             "k.match_id = team_match.match_id AND k.team_id = ? AND k.source = team_match.source)",
             [dup, keep], f"team {dup} row already held by team {keep}")  # fmt: skip
    for table, col in TEAM_COLUMNS:
        j.move(table, col, dup, keep, f"team {dup} merged into {keep}")
    j.delete("teams", "team_id = ?", [dup], f"merged into {keep}")


def _merge_duplicate_matches(j: Journal) -> int:
    """Matches of one fixture stored twice: keep the first, fill its gaps from the other and
    move the other's rows onto it (a row both already have is kept from the first)."""
    db = j.db
    rows = db.con.execute(
        "SELECT min(match_id), list(match_id ORDER BY match_id) FROM matches WHERE "
        f"competition_id NOT IN ({', '.join(repr(c) for c in sorted(REPEATING))}) "
        "GROUP BY competition_id, season, home_team_id, away_team_id HAVING count(*) > 1 "
        "UNION ALL SELECT min(match_id), list(match_id ORDER BY match_id) FROM matches WHERE "
        f"competition_id IN ({', '.join(repr(c) for c in sorted(REPEATING))}) "
        "GROUP BY competition_id, season, home_team_id, away_team_id, match_date "
        "HAVING count(*) > 1"
    ).fetchall()
    n = 0
    for keep, ids in rows:
        for dup in ids[1:]:
            j.rewrite("matches", "match_id = ?", [keep], lambda dup=dup, keep=keep: db.con.execute(
                "UPDATE matches SET matchday = coalesce(matches.matchday, d.matchday), "
                "kickoff = coalesce(matches.kickoff, d.kickoff), "
                "home_goals = coalesce(matches.home_goals, d.home_goals), "
                "away_goals = coalesce(matches.away_goals, d.away_goals), "
                "finished = coalesce(matches.finished, false) OR coalesce(d.finished, false) "
                "FROM (SELECT * FROM matches WHERE match_id = ?) d WHERE matches.match_id = ?",
                [dup, keep]), f"gaps filled from duplicate {dup}")  # fmt: skip
            why = f"match {dup} merged into {keep}"
            for table, col in REFERENCES["match"]:
                if table == "matches":
                    continue
                cols = MATCH_CHILDREN.get(table)
                if cols and table != "match_ids":
                    same = " AND ".join(f"k.{c} = {table}.{c}" for c in cols)
                    j.delete(table, f"match_id = ? AND EXISTS (SELECT 1 FROM {table} k WHERE "
                             f"k.match_id = ? AND {same})", [dup, keep],
                             f"{why}; the kept match already has this row")  # fmt: skip
                j.move(table, col, dup, keep, why)
            j.delete("matches", "match_id = ?", [dup], why)
            n += 1
    return n


def quarantine(db: FootballDB) -> list[Issue]:
    """Remove individually impossible rows and report each removal as a warning.

    One bad shot or one mis-mapped match must not throw away a multi-hour load, and it must not
    be used either, so the rows are dropped and named (with enough context to find the cause).
    Anything systematic is still caught by `run_checks`, which runs after this. Every removed
    row is kept in `repair_log` (see `rollback_repair`).
    """
    out: list[Issue] = []
    j = Journal(db, "quarantine")
    with db.transaction():
        n = j.delete("shots", "xg < 0 OR xg > 1 OR x < 0 OR x > 1 OR y < 0 OR y > 1 "
                     "OR minute < 0 OR minute > 130", [], "impossible shot value")  # fmt: skip
        if n:
            out.append(
                Issue("warn", "quarantined_shots", f"dropped {n} shots with impossible values")
            )
        n = j.delete("fpl_players_gw", "now_cost < 30 OR now_cost > 250", [],
                     "price outside the possible range")  # fmt: skip
        if n:
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
            for table in MATCH_TABLES:
                j.delete(table, f"match_id IN ({marks})", ids, "a club playing itself")
        out += _quarantine_scores(j)
    return out


SCORE_QUARANTINE_LIMIT = 0.05  # beyond this share something systematic is broken: fail instead


def _quarantine_scores(j: Journal) -> list[Issue]:
    """Matches whose sources disagree on the score are dropped (and their FotMob pages marked
    unread, so a fixed loader stores them again), as long as they are a small share. One wrong
    result must not stop a whole backfill from saving; a broken source or mapping still does."""
    db = j.db
    bad = db.query(
        "SELECT DISTINCT m.match_id FROM matches m JOIN team_match t ON t.match_id = m.match_id "
        "AND t.team_id = m.home_team_id WHERE m.finished AND t.goals_for <> m.home_goals"
    )
    finished = max(_count(db, "SELECT count(*) FROM matches WHERE finished"), 1)
    if bad.empty or len(bad) / finished > SCORE_QUARANTINE_LIMIT:
        return []
    where = _where(db)
    ids = [int(i) for i in bad["match_id"]]
    marks = ", ".join("?" for _ in ids)
    j.delete("ingest_state", "source = 'fotmob' AND key IN (SELECT source_id FROM match_ids "
             f"WHERE source = 'fotmob' AND match_id IN ({marks}))", ids,
             "page of a match whose sources disagree: read again")  # fmt: skip
    for table in MATCH_TABLES:
        j.delete(table, f"match_id IN ({marks})", ids, "sources disagree on the score")
    return [Issue("warn", "quarantined_scores",
                  f"dropped {len(ids)} matches where sources disagree on the score ({where})")]  # fmt: skip
