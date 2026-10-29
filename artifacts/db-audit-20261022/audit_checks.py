# ruff: noqa: E501
"""DB02: read-only, full-population integrity and football-data checks.

Run from the repo root:  .venv/Scripts/python.exe artifacts/db-audit-20261022/audit_checks.py
Writes checks.json (pass/fail per check with denominators) and measurements.json (coverage,
freshness, temporal facts). The database is opened read-only; nothing is repaired or cleaned.
Examples are ids only (no names, no private payloads) and capped at 3 rows.
"""

from __future__ import annotations

import json
import sys
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path

import duckdb

ROOT = Path.cwd()
OUT = Path(__file__).parent
DB = ROOT / "data_cache" / "football.duckdb"
con = duckdb.connect(str(DB), read_only=True)


@dataclass
class Check:
    id: str
    area: str  # keys | nulls | orphans | domain | numeric | json | identity | joins | reconcile | time
    objects: str
    description: str
    expect: str  # "zero" violations expected, or "info" (measurement)
    violations_sql: str
    denominator_sql: str
    example_sql: str = ""
    classification: str = ""  # ok | finding id and class | disposition
    columns: list = field(default_factory=list)  # table.column pairs the SQL references
    code_ref: str = ""
    result: dict = field(default_factory=dict)


CHECKS: list[Check] = []


def add(id, area, objects, desc, viol, denom, example="", expect="zero", code_ref=""):
    CHECKS.append(Check(id, area, objects, desc, expect, viol, denom, example, code_ref=code_ref))


def one(sql: str):
    return con.execute(sql).fetchone()[0]


# ---------------------------------------------------------------------------------------
# 1. keys and duplicates: declared PKs are enforced by DuckDB, so test natural keys of the
#    tables that declare none and the cross-source uniqueness the PK cannot express
# ---------------------------------------------------------------------------------------
NO_PK = {
    "projections": "run_id, model, season, gw, player_id",
    "decisions": "season, gw, created_at",
    "reviews": "season, gw, created_at, kind",
    "lessons": "season, gw, kind, created_at",
    "match_odds_log": "match_id, source, bookmaker, closing, ingested_at",
    "transfers": "player_id, from_team_id, to_team_id, date",
    "ingest_log": "source, run_at, table_name",
    "scout_calls": "called_at, model",
}
for t, key in NO_PK.items():
    add(
        f"key.{t}.natural_unique",
        "keys",
        t,
        f"({key}) is unique in {t}, which declares no key",
        f"SELECT count(*) FROM (SELECT 1 FROM {t} GROUP BY {key} HAVING count(*) > 1)",
        f"SELECT count(*) FROM (SELECT 1 FROM {t} GROUP BY {key})",
    )

add(
    "key.teams.key_unique",
    "keys",
    "teams",
    "club key is unique",
    "SELECT count(*) FROM (SELECT 1 FROM teams GROUP BY key HAVING count(*) > 1)",
    "SELECT count(*) FROM teams",
    code_ref="quality.py:48",
)
add(
    "key.team_ids.one_per_source",
    "keys",
    "team_ids",
    "a club has at most one id per source",
    "SELECT count(*) FROM (SELECT 1 FROM team_ids GROUP BY team_id, source HAVING count(*) > 1)",
    "SELECT count(*) FROM (SELECT 1 FROM team_ids GROUP BY team_id, source)",
)
add(
    "key.player_ids.one_per_source",
    "keys",
    "player_ids",
    "a canonical player has at most one id per source",
    "SELECT count(*) FROM (SELECT 1 FROM player_ids GROUP BY player_id, source HAVING count(*) > 1)",
    "SELECT count(*) FROM (SELECT 1 FROM player_ids GROUP BY player_id, source)",
    code_ref="quality.py:74; database.py:253",
)
add(
    "key.matches.league_pairing_once",
    "keys",
    "matches",
    "an ENG1 pairing exists once per season",
    "SELECT count(*) FROM (SELECT 1 FROM matches WHERE competition_id='ENG1' GROUP BY season, "
    "home_team_id, away_team_id HAVING count(*) > 1)",
    "SELECT count(*) FROM matches WHERE competition_id='ENG1'",
    code_ref="quality.py:42",
)
add(
    "key.matches.cup_pairing_date_once",
    "keys",
    "matches",
    "a cup pairing exists once per (season, comp, home, away, date)",
    "SELECT count(*) FROM (SELECT 1 FROM matches WHERE competition_id<>'ENG1' GROUP BY "
    "competition_id, season, home_team_id, away_team_id, match_date HAVING count(*) > 1)",
    "SELECT count(*) FROM matches WHERE competition_id<>'ENG1'",
)
add(
    "key.fpl_players_gw.one_snapshot_per_gw_player",
    "joins",
    "fpl_players_gw",
    "(season, gw, fpl_id) has several snapshots, so a join on that key multiplies rows "
    "(design risk: every consumer must pick one snapshot)",
    "SELECT count(*) FROM (SELECT 1 FROM fpl_players_gw GROUP BY season, gw, fpl_id HAVING count(*) > 1)",
    "SELECT count(*) FROM (SELECT 1 FROM fpl_players_gw GROUP BY season, gw, fpl_id)",
    expect="info",
    code_ref="database.py:381",
)
add(
    "key.team_match.sources_per_team_match",
    "joins",
    "team_match",
    "(match_id, team_id) has rows from several sources, so a join on it multiplies rows",
    "SELECT count(*) FROM (SELECT 1 FROM team_match GROUP BY match_id, team_id HAVING count(*) > 1)",
    "SELECT count(*) FROM (SELECT 1 FROM team_match GROUP BY match_id, team_id)",
    expect="info",
)
add(
    "key.match_odds.rows_per_match",
    "joins",
    "match_odds",
    "a match has several odds rows (avg/max x opening/closing); join on match_id multiplies rows",
    "SELECT count(*) FROM (SELECT 1 FROM match_odds GROUP BY match_id HAVING count(*) > 1)",
    "SELECT count(*) FROM (SELECT 1 FROM match_odds GROUP BY match_id)",
    expect="info",
)

# ---------------------------------------------------------------------------------------
# 2. required nulls
# ---------------------------------------------------------------------------------------
REQUIRED = {
    "matches": [
        "competition_id",
        "season",
        "matchday",
        "kickoff",
        "match_date",
        "home_team_id",
        "away_team_id",
        "finished",
        "source",
        "ingested_at",
    ],
    "teams": ["name", "key"],
    "players": ["name", "position"],
    "appearances": ["team_id", "started", "minutes", "position", "ingested_at"],
    "team_match": ["is_home", "goals_for", "goals_against", "ingested_at"],
    "player_match_stats": ["value", "ingested_at"],
    "shots": ["match_id", "player_id", "minute", "xg", "x", "y", "result"],
    "fpl_players_gw": [
        "player_id",
        "team_id",
        "position",
        "now_cost",
        "selected_by_percent",
        "status",
        "total_points",
        "minutes",
        "raw",
    ],
    "fpl_points_gw": [
        "player_id",
        "minutes",
        "total_points",
        "bps",
        "bonus",
        "stats",
        "explain",
        "ingested_at",
    ],
    "projections": ["xpts", "detail", "created_at"],
    "match_odds": ["home", "draw", "away", "ingested_at"],
    "runs": ["season", "gw", "entry_id", "mode", "started_at", "status"],
    "decisions": ["payload", "mode"],
}
for t, cols in REQUIRED.items():
    for c in cols:
        add(
            f"null.{t}.{c}",
            "nulls",
            f"{t}.{c}",
            f"{t}.{c} is populated",
            f"SELECT count(*) FROM {t} WHERE {c} IS NULL",
            f"SELECT count(*) FROM {t}",
        )

# ---------------------------------------------------------------------------------------
# 3. orphans (broader than quality.py's list)
# ---------------------------------------------------------------------------------------
FKS = [
    ("match_ids", "match_id", "matches", "match_id"),
    ("team_ids", "team_id", "teams", "team_id"),
    ("player_ids", "player_id", "players", "player_id"),
    ("matches", "competition_id", "competitions", "competition_id"),
    ("matches", "home_team_id", "teams", "team_id"),
    ("matches", "away_team_id", "teams", "team_id"),
    ("team_match", "match_id", "matches", "match_id"),
    ("team_match", "team_id", "teams", "team_id"),
    ("match_odds", "match_id", "matches", "match_id"),
    ("match_odds_log", "match_id", "matches", "match_id"),
    ("appearances", "match_id", "matches", "match_id"),
    ("appearances", "player_id", "players", "player_id"),
    ("appearances", "team_id", "teams", "team_id"),
    ("player_match_stats", "match_id", "matches", "match_id"),
    ("player_match_stats", "player_id", "players", "player_id"),
    ("shots", "match_id", "matches", "match_id"),
    ("shots", "player_id", "players", "player_id"),
    ("fpl_players_gw", "player_id", "players", "player_id"),
    ("fpl_players_gw", "team_id", "teams", "team_id"),
    ("fpl_points_gw", "player_id", "players", "player_id"),
    ("projections", "player_id", "players", "player_id"),
    ("transfers", "player_id", "players", "player_id"),
    ("runs", "projection_run", "projections", "run_id"),
]
for t, c, p, pc in FKS:
    add(
        f"orphan.{t}.{c}",
        "orphans",
        f"{t}.{c}->{p}.{pc}",
        f"every {t}.{c} exists in {p}.{pc}",
        f"SELECT count(*) FROM {t} x WHERE x.{c} IS NOT NULL AND NOT EXISTS "
        f"(SELECT 1 FROM {p} y WHERE y.{pc} = x.{c})",
        f"SELECT count(*) FROM {t} WHERE {c} IS NOT NULL",
        f"SELECT x.{c} FROM {t} x WHERE x.{c} IS NOT NULL AND NOT EXISTS "
        f"(SELECT 1 FROM {p} y WHERE y.{pc} = x.{c}) LIMIT 3",
    )
add(
    "orphan.projections.run_in_runs",
    "orphans",
    "projections.run_id->runs.projection_run",
    "every projection run is referenced by an agent run (model-only runs are not)",
    "SELECT count(DISTINCT run_id) FROM projections WHERE run_id NOT IN "
    "(SELECT projection_run FROM runs WHERE projection_run IS NOT NULL)",
    "SELECT count(DISTINCT run_id) FROM projections",
    expect="info",
)
add(
    "orphan.decisions.run",
    "orphans",
    "decisions->runs",
    "each decision has a run with that decision_at",
    "SELECT count(*) FROM decisions d WHERE NOT EXISTS (SELECT 1 FROM runs r WHERE r.decision_at = d.created_at)",
    "SELECT count(*) FROM decisions",
)
add(
    "orphan.runs.snapshot",
    "orphans",
    "runs.snapshot_at->fpl_players_gw.snapshot_at",
    "each run's snapshot exists",
    "SELECT count(*) FROM runs r WHERE r.snapshot_at IS NOT NULL AND NOT EXISTS "
    "(SELECT 1 FROM fpl_players_gw g WHERE g.snapshot_at = r.snapshot_at)",
    "SELECT count(*) FROM runs WHERE snapshot_at IS NOT NULL",
)
add(
    "orphan.fpl_points.player_id_agrees",
    "orphans",
    "fpl_points_gw.player_id",
    "player_id matches the player_ids mapping of fpl_id",
    "SELECT count(*) FROM fpl_points_gw f WHERE f.player_id IS DISTINCT FROM (SELECT i.player_id "
    "FROM player_ids i WHERE i.source='fpl' AND i.source_id = f.fpl_id::VARCHAR)",
    "SELECT count(*) FROM fpl_points_gw",
)
add(
    "orphan.fpl_points.fixture_mapped",
    "orphans",
    "fpl_points_gw.fixture_id->match_ids",
    "each FPL fixture id maps to a match (fixture 0 placeholders count as violations)",
    "SELECT count(*) FROM fpl_points_gw f WHERE NOT EXISTS (SELECT 1 FROM match_ids i WHERE "
    "i.source='fpl' AND i.source_id = f.fixture_id::VARCHAR)",
    "SELECT count(*) FROM fpl_points_gw",
    code_ref="ingest_fpl.py:166",
)
add(
    "orphan.field_ownership.fpl_id",
    "orphans",
    "field_ownership.fpl_id",
    "ownership rows refer to a known FPL player",
    "SELECT count(*) FROM field_ownership f WHERE NOT EXISTS (SELECT 1 FROM fpl_players_gw g WHERE g.fpl_id = f.fpl_id)",
    "SELECT count(*) FROM field_ownership",
)
add(
    "orphan.players_without_id",
    "orphans",
    "players",
    "each player has a source id row",
    "SELECT count(*) FROM players p WHERE NOT EXISTS (SELECT 1 FROM player_ids i WHERE i.player_id = p.player_id)",
    "SELECT count(*) FROM players",
)
add(
    "orphan.teams_without_id",
    "orphans",
    "teams",
    "each club has a source id row",
    "SELECT count(*) FROM teams p WHERE NOT EXISTS (SELECT 1 FROM team_ids i WHERE i.team_id = p.team_id)",
    "SELECT count(*) FROM teams",
)
add(
    "orphan.matches_without_id",
    "orphans",
    "matches",
    "each match has a source id row",
    "SELECT count(*) FROM matches p WHERE NOT EXISTS (SELECT 1 FROM match_ids i WHERE i.match_id = p.match_id)",
    "SELECT count(*) FROM matches",
)

# ---------------------------------------------------------------------------------------
# 4. domains and ranges
# ---------------------------------------------------------------------------------------
DOMAIN = [
    ("matches", "same_team", "home_team_id = away_team_id"),
    ("matches", "finished_needs_score", "finished AND (home_goals IS NULL OR away_goals IS NULL)"),
    (
        "matches",
        "unfinished_has_score",
        "NOT finished AND (home_goals IS NOT NULL OR away_goals IS NOT NULL)",
    ),
    (
        "matches",
        "negative_goals",
        "home_goals < 0 OR away_goals < 0 OR home_goals > 15 OR away_goals > 15",
    ),
    ("matches", "date_matches_kickoff", "match_date IS DISTINCT FROM CAST(kickoff AS DATE)"),
    ("matches", "finished_in_future", "finished AND kickoff > now()"),
    (
        "matches",
        "stale_unfinished_past_kickoff",
        "NOT finished AND kickoff < now() - INTERVAL 4 HOUR",
    ),
    ("appearances", "minutes_range", "minutes < 0 OR minutes > 130"),
    ("appearances", "started_zero_minutes", "started AND minutes = 0"),
    ("appearances", "position_enum", "position NOT IN ('GKP','DEF','MID','FWD')"),
    ("players", "position_enum", "position NOT IN ('GKP','DEF','MID','FWD')"),
    ("fpl_players_gw", "position_enum", "position NOT IN ('GKP','DEF','MID','FWD')"),
    ("fpl_players_gw", "price_range", "now_cost < 30 OR now_cost > 250"),
    ("fpl_players_gw", "status_enum", "status NOT IN ('a','d','i','n','s','u')"),
    ("fpl_players_gw", "chance_range", "chance_next < 0 OR chance_next > 100"),
    ("fpl_players_gw", "ownership_range", "selected_by_percent < 0 OR selected_by_percent > 100"),
    (
        "fpl_players_gw",
        "available_without_chance_rule",
        "status <> 'a' AND chance_next IS NULL AND status IN ('d')",
    ),
    ("fpl_points_gw", "minutes_range", "minutes < 0 OR minutes > 130"),
    ("fpl_points_gw", "bonus_range", "bonus < 0 OR bonus > 3"),
    (
        "fpl_points_gw",
        "stats_minutes_agree",
        "minutes IS DISTINCT FROM (stats->>'minutes')::INTEGER",
    ),
    (
        "fpl_points_gw",
        "stats_total_agree",
        "total_points IS DISTINCT FROM (stats->>'total_points')::INTEGER",
    ),
    (
        "fpl_points_gw",
        "explain_sums_to_total",
        "total_points <> coalesce(list_sum(list_transform(json_transform(explain, "
        '\'[{"points":"INTEGER"}]\'), x -> x.points)), 0)',
    ),
    (
        "team_match",
        "goals_range",
        "goals_for < 0 OR goals_for > 15 OR goals_against < 0 OR goals_against > 15",
    ),
    ("team_match", "xg_range", "xg_for < 0 OR xg_for > 12 OR xg_against < 0 OR xg_against > 12"),
    (
        "team_match",
        "counts_nonnegative",
        "shots < 0 OR shots_on_target < 0 OR corners < 0 OR fouls < 0 OR yellows < 0 OR reds < 0",
    ),
    ("team_match", "sot_le_shots", "shots_on_target > shots"),
    ("shots", "xg_range", "xg < 0 OR xg > 1"),
    ("shots", "xy_range", "x < 0 OR x > 1 OR y < 0 OR y > 1"),
    ("shots", "minute_range", "minute < 0 OR minute > 130"),
    (
        "match_odds",
        "decimal_odds_gt_1",
        "home <= 1 OR draw <= 1 OR away <= 1 OR over25 <= 1 OR under25 <= 1",
    ),
    (
        "match_odds",
        "overround_range",
        "bookmaker = 'avg' AND (1/home + 1/draw + 1/away < 1 OR 1/home + 1/draw + 1/away > 1.25)",
    ),
    ("match_odds_log", "decimal_odds_gt_1", "home <= 1 OR draw <= 1 OR away <= 1"),
    ("projections", "xpts_range", "xpts < 0 OR xpts > 30"),
    ("field_ownership", "owned_range", "owned < 0 OR owned > 100"),
    ("field_ownership", "eo_nonnegative", "eo < 0"),
    ("field_ownership", "sample_positive", "sample <= 0"),
    ("runs", "time_order", "ended_at < started_at"),
    ("runs", "ok_without_decision", "status = 'ok' AND decision_at IS NULL"),
    ("runs", "status_enum", "status NOT IN ('ok','error','running','blocked')"),
    ("player_match_stats", "negative_unexpected", "value < 0 AND stat NOT IN ('goals_prevented')"),
]
for t, name, where in DOMAIN:
    add(
        f"domain.{t}.{name}",
        "domain",
        t,
        f"{t}: NOT ({where})",
        f"SELECT count(*) FROM {t} WHERE {where}",
        f"SELECT count(*) FROM {t}",
    )

# NaN / infinity across every DOUBLE column, mechanically
for t, c in con.execute(
    "SELECT table_name, column_name FROM duckdb_columns() WHERE schema_name='main' AND NOT internal "
    "AND data_type IN ('DOUBLE','FLOAT') ORDER BY 1, 2"
).fetchall():
    add(
        f"numeric.{t}.{c}.finite",
        "numeric",
        f"{t}.{c}",
        f"{t}.{c} has no NaN or infinity",
        f"SELECT count(*) FROM {t} WHERE isnan({c}) OR isinf({c})",
        f"SELECT count(*) FROM {t} WHERE {c} IS NOT NULL",
    )

# JSON validity and shape for every JSON column
for t, c in con.execute(
    "SELECT table_name, column_name FROM duckdb_columns() WHERE schema_name='main' AND NOT internal "
    "AND data_type = 'JSON' ORDER BY 1, 2"
).fetchall():
    add(
        f"json.{t}.{c}.valid",
        "json",
        f"{t}.{c}",
        f"{t}.{c} is valid JSON",
        f"SELECT count(*) FROM {t} WHERE {c} IS NOT NULL AND NOT json_valid({c}::VARCHAR)",
        f"SELECT count(*) FROM {t} WHERE {c} IS NOT NULL",
    )
add(
    "json.fpl_players_gw.raw.agrees_with_columns",
    "json",
    "fpl_players_gw.raw",
    "id, now_cost and total_points in raw equal the extracted columns",
    "SELECT count(*) FROM fpl_players_gw WHERE (raw->>'id')::INTEGER <> fpl_id OR "
    "(raw->>'now_cost')::INTEGER <> now_cost OR (raw->>'total_points')::INTEGER <> total_points",
    "SELECT count(*) FROM fpl_players_gw",
)
add(
    "json.decisions.payload.keys",
    "json",
    "decisions.payload",
    "payload carries the keys the dashboard and review read",
    "SELECT count(*) FROM decisions WHERE json_extract(payload, '$.xi') IS NULL OR "
    "json_extract(payload, '$.captain') IS NULL OR json_extract(payload, '$.action') IS NULL",
    "SELECT count(*) FROM decisions",
)
add(
    "json.projections.detail.keys",
    "json",
    "projections.detail",
    "detail has exp_minutes, q10, q90, p_zero",
    "SELECT count(*) FROM projections WHERE json_extract(detail, '$.exp_minutes') IS NULL OR "
    "json_extract(detail, '$.q10') IS NULL OR json_extract(detail, '$.q90') IS NULL OR "
    "json_extract(detail, '$.p_zero') IS NULL",
    "SELECT count(*) FROM projections",
)

# ---------------------------------------------------------------------------------------
# 5. identity: canonical ids across sources
# ---------------------------------------------------------------------------------------
add(
    "identity.player_link.name_similarity",
    "identity",
    "player_ids",
    "players linked across sources have similar source names (jaro-winkler < 0.80 suggests a wrong merge)",
    "SELECT count(*) FROM (SELECT a.player_id FROM player_ids a JOIN player_ids b ON a.player_id = b.player_id "
    "AND a.source < b.source WHERE jaro_winkler_similarity(lower(strip_accents(a.source_name)), "
    "lower(strip_accents(b.source_name))) < 0.80)",
    "SELECT count(*) FROM (SELECT a.player_id FROM player_ids a JOIN player_ids b ON a.player_id = b.player_id AND a.source < b.source)",
    "SELECT a.player_id FROM player_ids a JOIN player_ids b ON a.player_id = b.player_id AND a.source < b.source "
    "WHERE jaro_winkler_similarity(lower(strip_accents(a.source_name)), lower(strip_accents(b.source_name))) < 0.80 LIMIT 3",
    expect="info",
    code_ref="database.py:253",
)
add(
    "identity.player_link.position_agrees",
    "identity",
    "players/appearances",
    "a linked player's FotMob appearance position equals the canonical position",
    "SELECT count(*) FROM (SELECT DISTINCT a.player_id FROM appearances a JOIN players p USING (player_id) "
    "WHERE a.position IS NOT NULL AND a.position <> p.position AND p.player_id IN "
    "(SELECT player_id FROM player_ids WHERE source='fpl'))",
    "SELECT count(DISTINCT a.player_id) FROM appearances a WHERE a.player_id IN (SELECT player_id FROM player_ids WHERE source='fpl')",
    expect="info",
)
add(
    "identity.namesake_split_candidates",
    "identity",
    "players",
    "same lower-case name AND same position held by two player rows (possible unlinked duplicate person)",
    "SELECT count(*) FROM (SELECT lower(strip_accents(name)) n, position FROM players GROUP BY 1, 2 HAVING count(*) > 1)",
    "SELECT count(*) FROM (SELECT 1 FROM players GROUP BY lower(strip_accents(name)), position)",
    expect="info",
    code_ref="quality.py:51",
)
add(
    "identity.fpl_players_without_fotmob_link",
    "identity",
    "player_ids",
    "FPL players that played minutes but have no FotMob id (no xG/underlying stats)",
    "SELECT count(*) FROM (SELECT p.player_id FROM fpl_points_gw f JOIN player_ids p ON p.source='fpl' "
    "AND p.source_id = f.fpl_id::VARCHAR GROUP BY 1 HAVING sum(f.minutes) > 0 AND p.player_id NOT IN "
    "(SELECT player_id FROM player_ids WHERE source='fotmob'))",
    "SELECT count(*) FROM (SELECT p.player_id FROM fpl_points_gw f JOIN player_ids p ON p.source='fpl' "
    "AND p.source_id = f.fpl_id::VARCHAR GROUP BY 1 HAVING sum(f.minutes) > 0)",
    expect="info",
)
add(
    "identity.fpl_minutes_without_fotmob_link",
    "identity",
    "player_ids",
    "share of FPL minutes belonging to unlinked players",
    "SELECT coalesce(sum(f.minutes),0) FROM fpl_points_gw f JOIN player_ids p ON p.source='fpl' AND "
    "p.source_id = f.fpl_id::VARCHAR WHERE p.player_id NOT IN (SELECT player_id FROM player_ids WHERE source='fotmob')",
    "SELECT sum(minutes) FROM fpl_points_gw",
    expect="info",
)
add(
    "identity.fpl_ids_not_season_scoped",
    "identity",
    "player_ids/team_ids/match_ids",
    "FPL element, team and fixture ids are season-specific but the id tables key on (source, source_id) "
    "only; with one season loaded reuse cannot be observed (fixture needed)",
    "SELECT 0",
    "SELECT count(DISTINCT season) FROM fpl_players_gw",
    expect="info",
    code_ref="schema.py:11; database.py:231; ingest_fpl.py:36",
)
add(
    "identity.unlinked_marker_rows",
    "identity",
    "ingest_state",
    "name-link refusals recorded (source 'link'): single-word names and position disagreements",
    "SELECT count(*) FROM ingest_state WHERE source='link'",
    "SELECT count(*) FROM player_ids WHERE source='fotmob'",
    expect="info",
    code_ref="database.py:283",
)
add(
    "identity.fpl_current_team_vs_match_team",
    "identity",
    "fpl_points_gw/fpl_players_gw",
    "FPL point rows whose player's latest-snapshot club is not in the match (club changed or history "
    "lacks per-GW team: only latest snapshot exists)",
    "SELECT count(*) FROM fpl_points_gw f JOIN match_ids i ON i.source='fpl' AND i.source_id = f.fixture_id::VARCHAR "
    "JOIN matches m ON m.match_id = i.match_id JOIN (SELECT * FROM fpl_players_gw QUALIFY snapshot_at = "
    "max(snapshot_at) OVER (PARTITION BY fpl_id)) g ON g.fpl_id = f.fpl_id WHERE g.team_id NOT IN (m.home_team_id, m.away_team_id)",
    "SELECT count(*) FROM fpl_points_gw",
    expect="info",
    code_ref="ingest_fpl.py:156",
)

# ---------------------------------------------------------------------------------------
# 6. football reconciliation across tables and sources
# ---------------------------------------------------------------------------------------
RECON = [
    (
        "recon.team_match.home_score",
        "team_match",
        "team_match home goals equal matches.home_goals (any source)",
        "SELECT count(*) FROM matches m JOIN team_match t ON t.match_id=m.match_id AND t.team_id=m.home_team_id "
        "WHERE m.finished AND t.goals_for <> m.home_goals",
        "SELECT count(*) FROM matches m JOIN team_match t ON t.match_id=m.match_id AND t.team_id=m.home_team_id WHERE m.finished",
    ),
    (
        "recon.team_match.away_score",
        "team_match",
        "team_match away goals equal matches.away_goals (quality.py checks the home side only)",
        "SELECT count(*) FROM matches m JOIN team_match t ON t.match_id=m.match_id AND t.team_id=m.away_team_id "
        "WHERE m.finished AND t.goals_for <> m.away_goals",
        "SELECT count(*) FROM matches m JOIN team_match t ON t.match_id=m.match_id AND t.team_id=m.away_team_id WHERE m.finished",
    ),
    (
        "recon.team_match.is_home",
        "team_match",
        "is_home agrees with matches.home_team_id",
        "SELECT count(*) FROM team_match t JOIN matches m USING (match_id) WHERE t.is_home <> (t.team_id = m.home_team_id)",
        "SELECT count(*) FROM team_match",
    ),
    (
        "recon.team_match.team_in_match",
        "team_match",
        "team is one of the match's clubs",
        "SELECT count(*) FROM team_match t JOIN matches m USING (match_id) WHERE t.team_id NOT IN (m.home_team_id, m.away_team_id)",
        "SELECT count(*) FROM team_match",
    ),
    (
        "recon.team_match.for_against_mirror",
        "team_match",
        "one side's goals_for equals the other's goals_against",
        "SELECT count(*) FROM team_match t JOIN team_match u ON t.match_id=u.match_id AND t.source=u.source AND t.team_id<>u.team_id "
        "WHERE t.goals_for <> u.goals_against",
        "SELECT count(*) FROM team_match",
    ),
    (
        "recon.team_match.two_sides",
        "team_match",
        "every (match, source) has exactly two teams",
        "SELECT count(*) FROM (SELECT 1 FROM team_match GROUP BY match_id, source HAVING count(*) <> 2)",
        "SELECT count(*) FROM (SELECT 1 FROM team_match GROUP BY match_id, source)",
    ),
    (
        "recon.team_match.sources_agree_goals",
        "team_match",
        "football-data, understat and fotmob agree on goals",
        "SELECT count(*) FROM team_match a JOIN team_match b ON a.match_id=b.match_id AND a.team_id=b.team_id AND a.source<b.source "
        "WHERE a.goals_for <> b.goals_for",
        "SELECT count(*) FROM team_match a JOIN team_match b ON a.match_id=b.match_id AND a.team_id=b.team_id AND a.source<b.source",
    ),
    (
        "recon.team_match.xg_understat_vs_fotmob",
        "team_match",
        "understat and fotmob team xG differ by more than 0.5",
        "SELECT count(*) FROM team_match a JOIN team_match b ON a.match_id=b.match_id AND a.team_id=b.team_id "
        "AND a.source='understat' AND b.source='fotmob' WHERE abs(a.xg_for-b.xg_for) > 0.5",
        "SELECT count(*) FROM team_match a JOIN team_match b ON a.match_id=b.match_id AND a.team_id=b.team_id "
        "AND a.source='understat' AND b.source='fotmob'",
    ),
    (
        "recon.finished_without_team_match",
        "matches",
        "each finished match has team_match rows",
        "SELECT count(*) FROM matches m WHERE m.finished AND NOT EXISTS (SELECT 1 FROM team_match t WHERE t.match_id=m.match_id)",
        "SELECT count(*) FROM matches WHERE finished",
    ),
    (
        "recon.finished_without_appearances",
        "matches",
        "each finished match has appearances",
        "SELECT count(*) FROM matches m WHERE m.finished AND NOT EXISTS (SELECT 1 FROM appearances t WHERE t.match_id=m.match_id)",
        "SELECT count(*) FROM matches WHERE finished",
    ),
    (
        "recon.finished_without_shots",
        "matches",
        "each finished match has shots",
        "SELECT count(*) FROM matches m WHERE m.finished AND NOT EXISTS (SELECT 1 FROM shots t WHERE t.match_id=m.match_id)",
        "SELECT count(*) FROM matches WHERE finished",
    ),
    (
        "recon.finished_pl_without_odds",
        "matches",
        "each finished ENG1 match has odds",
        "SELECT count(*) FROM matches m WHERE m.finished AND m.competition_id='ENG1' AND NOT EXISTS (SELECT 1 FROM match_odds t WHERE t.match_id=m.match_id)",
        "SELECT count(*) FROM matches WHERE finished AND competition_id='ENG1'",
    ),
    (
        "recon.finished_cup_without_odds",
        "matches",
        "each finished European match has odds (none loaded)",
        "SELECT count(*) FROM matches m WHERE m.finished AND m.competition_id<>'ENG1' AND NOT EXISTS (SELECT 1 FROM match_odds t WHERE t.match_id=m.match_id)",
        "SELECT count(*) FROM matches WHERE finished AND competition_id<>'ENG1'",
    ),
    (
        "recon.upcoming_pl_without_odds",
        "matches",
        "each unfinished ENG1 match has a price (prospective odds)",
        "SELECT count(*) FROM matches m WHERE NOT m.finished AND m.competition_id='ENG1' AND NOT EXISTS "
        "(SELECT 1 FROM match_odds t WHERE t.match_id=m.match_id) AND NOT EXISTS (SELECT 1 FROM match_odds_log t WHERE t.match_id=m.match_id)",
        "SELECT count(*) FROM matches WHERE NOT finished AND competition_id='ENG1'",
    ),
    (
        "recon.appearances.starters_eq_11",
        "appearances",
        "11 starters per team per finished match",
        "SELECT count(*) FROM (SELECT 1 FROM appearances GROUP BY match_id, team_id HAVING sum(started::INTEGER) <> 11)",
        "SELECT count(*) FROM (SELECT 1 FROM appearances GROUP BY match_id, team_id)",
    ),
    (
        "recon.appearances.team_minutes",
        "appearances",
        "team minutes within 900-1300 (11 x 90 plus ET/injuries)",
        "SELECT count(*) FROM (SELECT 1 FROM appearances GROUP BY match_id, team_id HAVING sum(minutes) < 900 OR sum(minutes) > 1300)",
        "SELECT count(*) FROM (SELECT 1 FROM appearances GROUP BY match_id, team_id)",
    ),
    (
        "recon.appearances.team_in_match",
        "appearances",
        "appearance club is one of the match's clubs",
        "SELECT count(*) FROM appearances a JOIN matches m USING (match_id) WHERE a.team_id NOT IN (m.home_team_id, m.away_team_id)",
        "SELECT count(*) FROM appearances",
    ),
    (
        "recon.appearances.team_coverage",
        "appearances",
        "both clubs of a covered match have appearances",
        "SELECT count(*) FROM (SELECT match_id FROM appearances GROUP BY match_id HAVING count(DISTINCT team_id) <> 2)",
        "SELECT count(DISTINCT match_id) FROM appearances",
    ),
    (
        "recon.appearances.without_stats",
        "appearances",
        "appearance rows with no player_match_stats rows (absent stats are unknown, not zero)",
        "SELECT count(*) FROM appearances a WHERE NOT EXISTS (SELECT 1 FROM player_match_stats s WHERE s.match_id=a.match_id AND s.player_id=a.player_id)",
        "SELECT count(*) FROM appearances",
    ),
    (
        "recon.appearances.without_stats_played",
        "appearances",
        "players who PLAYED (minutes>0) with no stats rows",
        "SELECT count(*) FROM appearances a WHERE a.minutes > 0 AND NOT EXISTS (SELECT 1 FROM player_match_stats s WHERE s.match_id=a.match_id AND s.player_id=a.player_id)",
        "SELECT count(*) FROM appearances WHERE minutes > 0",
    ),
    (
        "recon.stats.player_without_appearance",
        "player_match_stats",
        "stats exist only for appearing players",
        "SELECT count(*) FROM player_match_stats s WHERE NOT EXISTS (SELECT 1 FROM appearances a WHERE a.match_id=s.match_id AND a.player_id=s.player_id)",
        "SELECT count(*) FROM player_match_stats",
    ),
    (
        "recon.stats.goals_vs_team_goals",
        "player_match_stats",
        "sum of player goals equals team goals (own goals, unlinked scorers explain differences)",
        "SELECT count(*) FROM (SELECT s.match_id, a.team_id FROM player_match_stats s JOIN appearances a USING (match_id, player_id) "
        "JOIN team_match t ON t.match_id=s.match_id AND t.team_id=a.team_id AND t.source='fotmob' WHERE s.stat='goals' "
        "GROUP BY s.match_id, a.team_id, t.goals_for HAVING sum(s.value) <> t.goals_for)",
        "SELECT count(*) FROM (SELECT s.match_id, a.team_id FROM player_match_stats s JOIN appearances a USING (match_id, player_id) "
        "WHERE s.stat='goals' GROUP BY 1, 2)",
    ),
    (
        "recon.shots.xg_vs_team_xg",
        "shots",
        "sum of shot xG equals fotmob team xG within 0.05 per match",
        "SELECT count(*) FROM (SELECT s.match_id FROM shots s GROUP BY s.match_id HAVING abs(sum(s.xg) - "
        "(SELECT sum(t.xg_for) FROM team_match t WHERE t.match_id=s.match_id AND t.source='fotmob')) > 0.05)",
        "SELECT count(DISTINCT match_id) FROM shots",
    ),
    (
        "recon.shots.goals_vs_score",
        "shots",
        "shots with result Goal equal the match's goals",
        "SELECT count(*) FROM (SELECT m.match_id FROM matches m JOIN shots s ON s.match_id=m.match_id WHERE m.finished "
        "GROUP BY m.match_id, m.home_goals, m.away_goals HAVING sum((s.result='Goal')::INTEGER) <> m.home_goals + m.away_goals)",
        "SELECT count(DISTINCT m.match_id) FROM matches m JOIN shots s ON s.match_id=m.match_id WHERE m.finished",
    ),
    (
        "recon.fpl_vs_fotmob.minutes",
        "fpl_points_gw/appearances",
        "FPL minutes equal FotMob minutes for the same player-match (FPL excludes stoppage beyond 90)",
        "WITH f AS (SELECT i.match_id, p.player_id, sum(x.minutes) fm FROM fpl_points_gw x JOIN match_ids i ON i.source='fpl' AND "
        "i.source_id=x.fixture_id::VARCHAR JOIN player_ids p ON p.source='fpl' AND p.source_id=x.fpl_id::VARCHAR GROUP BY 1,2) "
        "SELECT count(*) FROM f JOIN appearances a USING (match_id, player_id) WHERE f.fm <> a.minutes",
        "WITH f AS (SELECT i.match_id, p.player_id FROM fpl_points_gw x JOIN match_ids i ON i.source='fpl' AND "
        "i.source_id=x.fixture_id::VARCHAR JOIN player_ids p ON p.source='fpl' AND p.source_id=x.fpl_id::VARCHAR GROUP BY 1,2) "
        "SELECT count(*) FROM f JOIN appearances a USING (match_id, player_id)",
    ),
    (
        "recon.fpl_vs_fotmob.minutes_gt_1",
        "fpl_points_gw/appearances",
        "same, ignoring differences of 1-5 minutes (stoppage-time definitions)",
        "WITH f AS (SELECT i.match_id, p.player_id, sum(x.minutes) fm FROM fpl_points_gw x JOIN match_ids i ON i.source='fpl' AND "
        "i.source_id=x.fixture_id::VARCHAR JOIN player_ids p ON p.source='fpl' AND p.source_id=x.fpl_id::VARCHAR GROUP BY 1,2) "
        "SELECT count(*) FROM f JOIN appearances a USING (match_id, player_id) WHERE abs(f.fm - a.minutes) > 5",
        "WITH f AS (SELECT i.match_id, p.player_id FROM fpl_points_gw x JOIN match_ids i ON i.source='fpl' AND "
        "i.source_id=x.fixture_id::VARCHAR JOIN player_ids p ON p.source='fpl' AND p.source_id=x.fpl_id::VARCHAR GROUP BY 1,2) "
        "SELECT count(*) FROM f JOIN appearances a USING (match_id, player_id)",
    ),
    (
        "recon.fpl_played_not_in_fotmob",
        "fpl_points_gw/appearances",
        "linked FPL players with minutes in a FotMob-covered match but no appearance row",
        "WITH f AS (SELECT i.match_id, p.player_id FROM fpl_points_gw x JOIN match_ids i ON i.source='fpl' AND i.source_id=x.fixture_id::VARCHAR "
        "JOIN player_ids p ON p.source='fpl' AND p.source_id=x.fpl_id::VARCHAR WHERE x.minutes>0 AND p.player_id IN "
        "(SELECT player_id FROM player_ids WHERE source='fotmob')) SELECT count(*) FROM f WHERE f.match_id IN "
        "(SELECT match_id FROM appearances) AND NOT EXISTS (SELECT 1 FROM appearances a WHERE a.match_id=f.match_id AND a.player_id=f.player_id)",
        "SELECT count(*) FROM fpl_points_gw x JOIN player_ids p ON p.source='fpl' AND p.source_id=x.fpl_id::VARCHAR WHERE x.minutes>0 AND p.player_id IN "
        "(SELECT player_id FROM player_ids WHERE source='fotmob')",
    ),
    (
        "recon.fpl_vs_fotmob.goals",
        "fpl_points_gw/player_match_stats",
        "FPL goals equal FotMob goals for the same player-match",
        "WITH f AS (SELECT i.match_id, p.player_id, sum((x.stats->>'goals_scored')::INTEGER) g FROM fpl_points_gw x JOIN match_ids i ON "
        "i.source='fpl' AND i.source_id=x.fixture_id::VARCHAR JOIN player_ids p ON p.source='fpl' AND p.source_id=x.fpl_id::VARCHAR GROUP BY 1,2) "
        "SELECT count(*) FROM f JOIN player_match_stats s ON s.match_id=f.match_id AND s.player_id=f.player_id AND s.stat='goals' WHERE f.g <> s.value",
        "WITH f AS (SELECT i.match_id, p.player_id FROM fpl_points_gw x JOIN match_ids i ON i.source='fpl' AND i.source_id=x.fixture_id::VARCHAR "
        "JOIN player_ids p ON p.source='fpl' AND p.source_id=x.fpl_id::VARCHAR GROUP BY 1,2) SELECT count(*) FROM f JOIN player_match_stats s "
        "ON s.match_id=f.match_id AND s.player_id=f.player_id AND s.stat='goals'",
    ),
    (
        "recon.fpl.gw_equals_matchday",
        "fpl_points_gw/matches",
        "FPL gw equals the match's matchday",
        "SELECT count(*) FROM fpl_points_gw f JOIN match_ids i ON i.source='fpl' AND i.source_id=f.fixture_id::VARCHAR "
        "JOIN matches m ON m.match_id=i.match_id WHERE f.gw <> m.matchday",
        "SELECT count(*) FROM fpl_points_gw",
    ),
    (
        "recon.fpl.kickoff_equals_match",
        "fpl_points_gw/matches",
        "points kickoff equals matches.kickoff",
        "SELECT count(*) FROM fpl_points_gw f JOIN match_ids i ON i.source='fpl' AND i.source_id=f.fixture_id::VARCHAR "
        "JOIN matches m ON m.match_id=i.match_id WHERE f.kickoff IS DISTINCT FROM m.kickoff",
        "SELECT count(*) FROM fpl_points_gw",
    ),
    (
        "recon.fpl.snapshot_totals_equal_live_sum",
        "fpl_players_gw/fpl_points_gw",
        "latest snapshot total_points and minutes equal the sum of per-GW live rows",
        "SELECT count(*) FROM (SELECT * FROM fpl_players_gw QUALIFY snapshot_at = max(snapshot_at) OVER (PARTITION BY fpl_id)) s "
        "LEFT JOIN (SELECT fpl_id, sum(total_points) t, sum(minutes) m FROM fpl_points_gw GROUP BY 1) p USING (fpl_id) "
        "WHERE s.total_points <> coalesce(p.t,0) OR s.minutes <> coalesce(p.m,0)",
        "SELECT count(DISTINCT fpl_id) FROM fpl_players_gw",
    ),
    (
        "recon.fpl.gw_row_coverage",
        "fpl_points_gw",
        "every player has a row for every finished GW (absent row is not a zero)",
        "SELECT (SELECT count(DISTINCT fpl_id) FROM fpl_players_gw) * (SELECT count(DISTINCT gw) FROM fpl_points_gw) - count(*) FROM fpl_points_gw",
        "SELECT (SELECT count(DISTINCT fpl_id) FROM fpl_players_gw) * (SELECT count(DISTINCT gw) FROM fpl_points_gw)",
    ),
    (
        "recon.fixtures.one_per_team_per_gw",
        "matches",
        "each ENG1 club has exactly one fixture per matchday as stored (no BGW/DGW/postponement visible)",
        "SELECT count(*) FROM (SELECT matchday, t FROM (SELECT matchday, home_team_id t FROM matches WHERE competition_id='ENG1' UNION ALL "
        "SELECT matchday, away_team_id FROM matches WHERE competition_id='ENG1') GROUP BY 1, 2 HAVING count(*) <> 1)",
        "SELECT count(*) FROM (SELECT 1 FROM (SELECT matchday, home_team_id t FROM matches WHERE competition_id='ENG1' UNION ALL "
        "SELECT matchday, away_team_id FROM matches WHERE competition_id='ENG1') GROUP BY matchday, t)",
    ),
    (
        "recon.fixtures.38_per_club_19_home",
        "matches",
        "each ENG1 club has 38 fixtures, 19 home",
        "SELECT count(*) FROM (SELECT t FROM (SELECT home_team_id t, 1 h FROM matches WHERE competition_id='ENG1' UNION ALL "
        "SELECT away_team_id, 0 FROM matches WHERE competition_id='ENG1') GROUP BY t HAVING count(*) <> 38 OR sum(h) <> 19)",
        "SELECT count(DISTINCT home_team_id) FROM matches WHERE competition_id='ENG1'",
    ),
    (
        "recon.odds.max_ge_avg",
        "match_odds",
        "the 'max' price is never below the 'avg' price",
        "SELECT count(*) FROM match_odds a JOIN match_odds m ON a.match_id=m.match_id AND a.closing=m.closing AND a.bookmaker='avg' "
        "AND m.bookmaker='max' WHERE m.home < a.home OR m.draw < a.draw OR m.away < a.away",
        "SELECT count(*) FROM match_odds WHERE bookmaker='avg'",
    ),
    (
        "recon.odds.log_vs_current",
        "match_odds/match_odds_log",
        "every stored price has at least one log observation",
        "SELECT count(*) FROM match_odds o WHERE NOT EXISTS (SELECT 1 FROM match_odds_log l WHERE l.match_id=o.match_id AND "
        "l.source=o.source AND l.bookmaker=o.bookmaker AND l.closing=o.closing)",
        "SELECT count(*) FROM match_odds",
    ),
    (
        "recon.ingest_state.fotmob_has_match",
        "ingest_state/match_ids",
        "each fotmob 'done' marker has a match_ids row",
        "SELECT count(*) FROM ingest_state s WHERE s.source='fotmob' AND NOT EXISTS (SELECT 1 FROM match_ids i WHERE i.source='fotmob' AND i.source_id=s.key)",
        "SELECT count(*) FROM ingest_state WHERE source='fotmob'",
    ),
    (
        "recon.finished_fotmob_match_has_marker",
        "ingest_state/matches",
        "each finished fotmob-covered match has a done marker",
        "SELECT count(*) FROM matches m WHERE m.finished AND m.competition_id<>'ENG1' AND NOT EXISTS (SELECT 1 FROM match_ids i JOIN ingest_state s ON "
        "s.source='fotmob' AND s.key=i.source_id WHERE i.source='fotmob' AND i.match_id=m.match_id)",
        "SELECT count(*) FROM matches WHERE finished AND competition_id<>'ENG1'",
    ),
    (
        "recon.ingest_log.rows_vs_table",
        "ingest_log",
        "fotmob/match ingest_log rows equal appearances rows",
        "SELECT abs((SELECT sum(rows) FROM ingest_log WHERE source='fotmob' AND table_name='match') - (SELECT count(*) FROM appearances))",
        "SELECT count(*) FROM appearances",
    ),
]
for id, objects, desc, viol, denom in RECON:
    add(id, "reconcile", objects, desc, viol, denom)
for c in CHECKS:
    if c.id in {
        "recon.fpl_vs_fotmob.minutes",
        "recon.appearances.without_stats",
        "recon.appearances.without_stats_played",
        "recon.finished_cup_without_odds",
        "recon.upcoming_pl_without_odds",
        "recon.finished_pl_without_odds",
        "recon.odds.log_vs_current",
        "recon.fpl.gw_row_coverage",
        "recon.stats.goals_vs_team_goals",
        "recon.shots.xg_vs_team_xg",
        "recon.fpl_vs_fotmob.minutes_gt_1",
        "recon.appearances.team_coverage",
        "recon.team_match.xg_understat_vs_fotmob",
        "recon.team_match.sources_agree_goals",
        "recon.fpl_played_not_in_fotmob",
        "recon.shots.goals_vs_score",
    }:
        c.expect = "info"

# ---------------------------------------------------------------------------------------
# 7. time semantics
# ---------------------------------------------------------------------------------------
add(
    "time.results_ingested_before_final_whistle",
    "time",
    "appearances/team_match/player_match_stats",
    "result-bearing rows ingested before kickoff + 105 minutes (provisional)",
    "SELECT count(*) FROM appearances a JOIN matches m USING (match_id) WHERE a.ingested_at < m.kickoff + INTERVAL 105 MINUTE",
    "SELECT count(*) FROM appearances",
)
add(
    "time.fpl_points_kickoff_known_at",
    "time",
    "fpl_points_gw",
    "points rows loaded before the fixture could be finished",
    "SELECT count(*) FROM fpl_points_gw WHERE kickoff IS NOT NULL AND ingested_at < kickoff + INTERVAL 105 MINUTE AND minutes > 0",
    "SELECT count(*) FROM fpl_points_gw WHERE minutes > 0",
)
add(
    "time.snapshots_per_gw",
    "time",
    "fpl_players_gw",
    "gameweeks with FINISHED fixtures but no pre-deadline FPL snapshot (history cannot be replayed)",
    "SELECT count(*) FROM (SELECT matchday FROM matches WHERE competition_id='ENG1' AND finished GROUP BY 1) g "
    "WHERE g.matchday NOT IN (SELECT gw FROM fpl_players_gw)",
    "SELECT count(DISTINCT matchday) FROM matches WHERE competition_id='ENG1' AND finished",
    expect="info",
)
add(
    "time.match_odds_known_at",
    "time",
    "match_odds",
    "odds rows whose ingestion time is after kickoff (not a pre-match observation)",
    "SELECT count(*) FROM match_odds o JOIN matches m USING (match_id) WHERE o.ingested_at > m.kickoff",
    "SELECT count(*) FROM match_odds",
    expect="info",
    code_ref="project.py:127",
)
add(
    "time.projection_created_after_snapshot",
    "time",
    "projections/fpl_players_gw",
    "projection runs created before any snapshot existed",
    "SELECT count(*) FROM (SELECT DISTINCT run_id, created_at FROM projections) p WHERE NOT EXISTS (SELECT 1 FROM fpl_players_gw g WHERE g.snapshot_at <= p.created_at)",
    "SELECT count(DISTINCT run_id) FROM projections",
)
add(
    "time.matches_ingested_at_not_updated",
    "time",
    "matches",
    "matches finished but ingested_at earlier than the result row sources could have produced "
    "(ingested_at is first-insert time; results are UPDATEd later without a timestamp)",
    "SELECT count(*) FROM matches WHERE finished AND ingested_at < kickoff",
    "SELECT count(*) FROM matches WHERE finished",
    code_ref="database.py:373",
)
add(
    "time.no_deadline_column",
    "time",
    "schema",
    "no table stores the official FPL deadline (deadline_time)",
    "SELECT CASE WHEN count(*) = 0 THEN 1 ELSE 0 END FROM duckdb_columns() WHERE schema_name='main' AND column_name ILIKE '%deadline%'",
    "SELECT 1",
    expect="info",
)
add(
    "time.future_projection_horizon",
    "time",
    "projections",
    "projections for gameweeks whose fixtures are unfinished are labelled by created_at only",
    "SELECT count(*) FROM projections p JOIN (SELECT matchday, bool_and(finished) fin FROM matches WHERE competition_id='ENG1' GROUP BY 1) g ON g.matchday=p.gw WHERE g.fin",
    "SELECT count(*) FROM projections",
)

# ---------------------------------------------------------------------------------------
# 8. enum drift detectors and JSON payload contracts (added after Codex review)
#    Allowed sets come from code constants where one exists and otherwise from the values
#    observed at audit time: they detect drift, they are not an external specification.
# ---------------------------------------------------------------------------------------
SOURCES = "('fpl','fotmob','understat','football-data','odds-api')"
ENUMS = [
    ("shots", "result", "('Goal','AttemptSaved','Miss','Post')"),
    (
        "shots",
        "situation",
        "('RegularPlay','FromCorner','FastBreak','SetPiece','ThrowInSetPiece',"
        "'FreeKick','Penalty','IndividualPlay')",
    ),
    ("shots", "shot_type", "('LeftFoot','RightFoot','Header','OtherBodyParts')"),
    ("shots", "source", SOURCES),
    ("matches", "source", SOURCES),
    ("team_match", "source", SOURCES),
    ("appearances", "source", SOURCES),
    ("player_match_stats", "source", SOURCES),
    ("match_odds", "source", SOURCES),
    ("match_odds", "bookmaker", "('avg','max')"),
    ("match_ids", "source", SOURCES),
    ("runs", "mode", "('dry','live')"),
    ("decisions", "mode", "('dry','live')"),
    ("runs", "state", "('planned','none')"),
    ("ingest_state", "status", "('done','final','unlinked')"),
    ("ingest_state", "source", "('fotmob','fpl_points','link','repair','import','backfill')"),
    ("competitions", "competition_id", "('ENG1','ENG2','ENG3','UCL','UEL','UECL')"),
    (
        "player_match_stats",
        "stat",
        "('assists','blocks','cbirt','cbit','clearances',"
        "'defensive_actions','fouls','goals','goals_conceded','goals_prevented','interceptions',"
        "'key_passes','npxG','rating','recoveries','red_cards','saves','shots','shots_on_target',"
        "'tackles','touches','touches_opp_box','was_fouled','xA','xG','xGOT','xGOT_faced',"
        "'yellow_cards')",
    ),
]
for t, c, allowed in ENUMS:
    add(
        f"enum.{t}.{c}",
        "domain",
        f"{t}.{c}",
        f"{t}.{c} is one of the known values (drift detector)",
        f"SELECT count(*) FROM {t} WHERE {c} IS NOT NULL AND {c} NOT IN {allowed}",
        f"SELECT count(*) FROM {t} WHERE {c} IS NOT NULL",
    )
add(
    "enum.player_ids.source",
    "domain",
    "player_ids.source",
    "player id source is a known label",
    "SELECT count(*) FROM player_ids WHERE source NOT IN ('fpl','fotmob','understat') AND source NOT LIKE 'vaastav-%'",
    "SELECT count(*) FROM player_ids",
)
add(
    "enum.team_ids.source",
    "domain",
    "team_ids.source",
    "team id source is a known label",
    "SELECT count(*) FROM team_ids WHERE source NOT IN ('fpl','fotmob','understat','football-data') AND source NOT LIKE 'vaastav-team%'",
    "SELECT count(*) FROM team_ids",
)
add(
    "enum.news_evidence.state",
    "domain",
    "news_evidence.payload.state",
    "news state is known",
    "SELECT count(*) FROM news_evidence WHERE payload->>'state' NOT IN ('fresh','failed','undated','stale')",
    "SELECT count(*) FROM news_evidence",
)

PROJ_COMP = (
    "minutes",
    "goals",
    "assists",
    "clean_sheets",
    "goals_conceded",
    "saves",
    "pen_saved",
    "pen_missed",
    "cards",
    "own_goals",
    "defcon",
    "bonus",
)
comp_sum = " + ".join(f"coalesce((detail->'components'->>'{k}')::DOUBLE, 0)" for k in PROJ_COMP)
comp_missing = " OR ".join(f"json_extract(detail, '$.components.{k}') IS NULL" for k in PROJ_COMP)
add(
    "contract.projections.detail.components",
    "json",
    "projections.detail",
    "detail.components has all 12 scoring components",
    f"SELECT count(*) FROM projections WHERE {comp_missing}",
    "SELECT count(*) FROM projections",
)
add(
    "contract.projections.detail.components_sum_to_xpts",
    "json",
    "projections.detail/xpts",
    "components sum to xpts within 0.05",
    f"SELECT count(*) FROM projections WHERE abs(({comp_sum}) - xpts) > 0.05",
    "SELECT count(*) FROM projections",
)
add(
    "contract.projections.detail.quantile_order",
    "json",
    "projections.detail",
    "q10 <= q90 and p_zero in [0,1]",
    "SELECT count(*) FROM projections WHERE (detail->>'q10')::DOUBLE > (detail->>'q90')::DOUBLE "
    "OR (detail->>'p_zero')::DOUBLE < 0 OR (detail->>'p_zero')::DOUBLE > 1",
    "SELECT count(*) FROM projections",
)
add(
    "contract.fpl_players_gw.raw.keys",
    "json",
    "fpl_players_gw.raw",
    "raw element carries id, element_type, team, now_cost, total_points, minutes, status",
    "SELECT count(*) FROM fpl_players_gw WHERE json_extract(raw,'$.id') IS NULL OR "
    "json_extract(raw,'$.element_type') IS NULL OR json_extract(raw,'$.team') IS NULL OR "
    "json_extract(raw,'$.now_cost') IS NULL OR json_extract(raw,'$.total_points') IS NULL OR "
    "json_extract(raw,'$.minutes') IS NULL OR json_extract(raw,'$.status') IS NULL",
    "SELECT count(*) FROM fpl_players_gw",
)
add(
    "contract.fpl_points_gw.explain.shape",
    "json",
    "fpl_points_gw.explain",
    "explain is an array whose items have identifier, points, value",
    "SELECT count(*) FROM fpl_points_gw WHERE json_type(explain) <> 'ARRAY' OR "
    "list_bool_or(list_transform(json_transform(explain, "
    '\'[{"identifier":"VARCHAR","points":"INTEGER","value":"INTEGER"}]\'), '
    "x -> x.identifier IS NULL OR x.points IS NULL OR x.value IS NULL))",
    "SELECT count(*) FROM fpl_points_gw",
)
add(
    "contract.fpl_points_gw.stats.keys",
    "json",
    "fpl_points_gw.stats",
    "stats is an object with minutes and total_points",
    "SELECT count(*) FROM fpl_points_gw WHERE json_type(stats) <> 'OBJECT' OR "
    "json_extract(stats,'$.minutes') IS NULL OR json_extract(stats,'$.total_points') IS NULL",
    "SELECT count(*) FROM fpl_points_gw",
)
add(
    "contract.decisions.payload.shape",
    "json",
    "decisions.payload",
    "xi has 11 and bench 4 players; transfers_in/out are arrays of equal length; action present",
    "SELECT count(*) FROM decisions WHERE json_array_length(payload->'xi') <> 11 OR "
    "json_array_length(payload->'bench') <> 4 OR json_array_length(payload->'transfers_in') <> "
    "json_array_length(payload->'transfers_out') OR json_extract(payload,'$.action') IS NULL",
    "SELECT count(*) FROM decisions",
)
add(
    "contract.decisions.payload.players_exist",
    "json",
    "decisions.payload/players",
    "every player id in xi and bench exists in players",
    "SELECT count(*) FROM (SELECT unnest(from_json(d.payload->'xi', '[\"INTEGER\"]')) pid FROM decisions d "
    "UNION ALL SELECT unnest(from_json(d.payload->'bench', '[\"INTEGER\"]')) FROM decisions d) x "
    "WHERE pid NOT IN (SELECT player_id FROM players)",
    "SELECT 15 * count(*) FROM decisions",
)
add(
    "contract.runs.stages_reasons",
    "json",
    "runs.stages/reasons",
    "stages is an object with bootstrap; reasons is an array",
    "SELECT count(*) FROM runs WHERE json_type(stages) <> 'OBJECT' OR "
    "json_extract(stages,'$.bootstrap') IS NULL OR json_type(reasons) <> 'ARRAY'",
    "SELECT count(*) FROM runs",
)
add(
    "contract.news_evidence.payload.keys",
    "json",
    "news_evidence.payload",
    "payload has url, state, source, observed_at",
    "SELECT count(*) FROM news_evidence WHERE json_extract(payload,'$.url') IS NULL OR "
    "json_extract(payload,'$.state') IS NULL OR json_extract(payload,'$.source') IS NULL OR "
    "json_extract(payload,'$.observed_at') IS NULL",
    "SELECT count(*) FROM news_evidence",
)
add(
    "contract.news_evidence.url_matches_column",
    "json",
    "news_evidence.url/payload",
    "url column equals payload.url",
    "SELECT count(*) FROM news_evidence WHERE url IS DISTINCT FROM (payload->>'url')",
    "SELECT count(*) FROM news_evidence",
)

# ---------------------------------------------------------------------------------------
# 9. mechanical sweeps and remaining column rules (closing the coverage ledger)
# ---------------------------------------------------------------------------------------
FUTURE_OK = {"kickoff", "match_date", "expires_at", "date"}
for t, c, ty in con.execute(
    "SELECT table_name, column_name, data_type FROM duckdb_columns() WHERE schema_name='main' "
    "AND NOT internal ORDER BY 1, 2"
).fetchall():
    if ty in ("TIMESTAMP", "DATE") and c not in FUTURE_OK:
        add(
            f"time.{t}.{c}.not_future",
            "time",
            f"{t}.{c}",
            f"{t}.{c} is not later than now + 1 day and not before 2016",
            f"SELECT count(*) FROM {t} WHERE {c} > now() + INTERVAL 1 DAY OR {c} < TIMESTAMP '2016-01-01'",
            f"SELECT count(*) FROM {t} WHERE {c} IS NOT NULL",
        )
    if c == "season":
        add(
            f"domain.{t}.season_range",
            "domain",
            f"{t}.season",
            f"{t}.season between 2016 and 2100",
            f"SELECT count(*) FROM {t} WHERE season < 2016 OR season > 2100",
            f"SELECT count(*) FROM {t} WHERE season IS NOT NULL",
        )
    if c == "gw":
        add(
            f"domain.{t}.gw_range",
            "domain",
            f"{t}.gw",
            f"{t}.gw between 1 and 38",
            f"SELECT count(*) FROM {t} WHERE gw < 1 OR gw > 38",
            f"SELECT count(*) FROM {t} WHERE gw IS NOT NULL",
        )

EXTRA = [
    (
        "competitions",
        "name_country_tier",
        "name IS NULL OR trim(name) = '' OR country IS NULL OR tier NOT IN (0, 1, 2, 3)",
    ),
    (
        "fpl_players_gw",
        "transfers_nonnegative",
        "transfers_in_event < 0 OR transfers_out_event < 0",
    ),
    (
        "shots",
        "ids_present",
        "shot_id IS NULL OR trim(shot_id) = '' OR source_match_id IS NULL OR trim(source_match_id) = ''",
    ),
    ("team_ids", "source_id_present", "trim(source_id) = '' OR source_name IS NULL"),
    ("news_evidence", "observation_id_present", "trim(observation_id) = ''"),
    ("runs", "run_id_format", "NOT regexp_matches(run_id, '^[0-9a-f]{16}$')"),
    ("runs", "version_known", "version <> 1"),
    ("runs", "exit_code_range", "exit_code NOT BETWEEN 0 AND 10"),
    ("runs", "delivery_consistent", "(delivered IS NULL) <> (delivered_at IS NULL)"),
    ("ingest_state", "key_present", "trim(key) = ''"),
    ("projections", "run_id_format", "NOT regexp_matches(run_id, '^[0-9a-f]{12}$')"),
]
for t, name, where in EXTRA:
    add(
        f"domain.{t}.{name}",
        "domain",
        t,
        f"{t}: NOT ({where})",
        f"SELECT count(*) FROM {t} WHERE {where}",
        f"SELECT count(*) FROM {t}",
    )
add(
    "recon.shots.source_match_maps",
    "reconcile",
    "shots.source_match_id/match_ids",
    "every shot's source_match_id maps through match_ids to the shot's match_id",
    "SELECT count(*) FROM shots s WHERE NOT EXISTS (SELECT 1 FROM match_ids i WHERE i.source = s.source "
    "AND i.source_id = s.source_match_id AND i.match_id = s.match_id)",
    "SELECT count(*) FROM shots",
)
add(
    "recon.ingest_state.updated_after_creation",
    "time",
    "ingest_state.updated_at",
    "markers are not stamped before the data they cover existed (updated_at >= earliest ingest_log)",
    "SELECT count(*) FROM ingest_state WHERE updated_at < (SELECT min(run_at) FROM ingest_log) - INTERVAL 1 DAY",
    "SELECT count(*) FROM ingest_state",
)
add(
    "recon.field_ownership.fetched_after_gw_start",
    "time",
    "field_ownership.fetched_at",
    "ownership for gw g was fetched after the previous gameweek's last kickoff (it uses g-1 picks)",
    "SELECT count(*) FROM field_ownership f WHERE f.fetched_at < (SELECT max(kickoff) FROM matches m "
    "WHERE m.competition_id='ENG1' AND m.season = f.season AND m.matchday = f.gw - 1)",
    "SELECT count(*) FROM field_ownership",
)

# ---------------------------------------------------------------------------------------
# run
# ---------------------------------------------------------------------------------------
summary = {"pass": 0, "fail": 0, "info": 0, "error": 0}
for c in CHECKS:
    try:
        viol = one(c.violations_sql)
        den = one(c.denominator_sql)
        ex = []
        if viol and c.example_sql:
            ex = [list(map(str, r)) for r in con.execute(c.example_sql).fetchall()[:3]]
        status = "info" if c.expect == "info" else ("pass" if viol == 0 else "fail")
        c.result = {"status": status, "violations": viol, "denominator": den, "examples": ex}
    except Exception as e:  # a failing query is a finding in itself, never silently skipped
        status = "error"
        c.result = {"status": "error", "error": str(e)[:300]}
    summary[status] += 1

# ---- existing quality.py checks, run read-only through a shim (no FootballDB.__init__) ----
sys.path.insert(0, str(ROOT / "src"))
existing: dict = {}
try:
    from fpl_agent.db import quality
    from fpl_agent.db.database import FootballDB

    shim = FootballDB.__new__(FootballDB)
    shim.con = con
    issues = quality.run_checks(shim)
    existing = {
        "ran": True,
        "issues": [asdict(i) for i in issues],
        "errors": len(quality.errors(issues)),
    }
except Exception as e:
    existing = {"ran": False, "error": str(e)[:400]}


# ---- measurements: coverage / freshness / temporal facts -------------------------------
def rows(sql):
    cur = con.execute(sql)
    names = [d[0] for d in cur.description]
    return [
        dict(
            zip(
                names,
                map(lambda v: v if isinstance(v, int | float | str | type(None)) else str(v), r),
                strict=True,
            )
        )
        for r in cur.fetchall()
    ]


now_utc = datetime.now(UTC).replace(tzinfo=None)
meas = {
    "audit_time_utc": now_utc.isoformat(),
    "seasons_in_matches": rows("SELECT season, count(*) n FROM matches GROUP BY 1 ORDER BY 1"),
    "seasons_in_fpl_players_gw": rows(
        "SELECT season, count(*) n, count(DISTINCT snapshot_at) snapshots FROM fpl_players_gw GROUP BY 1"
    ),
    "coverage_by_competition": rows(
        "SELECT m.competition_id comp, m.season, count(*) matches, sum(m.finished::INTEGER) finished, "
        "sum((m.finished AND EXISTS (SELECT 1 FROM team_match t WHERE t.match_id=m.match_id))::INTEGER) with_team_match, "
        "sum((m.finished AND EXISTS (SELECT 1 FROM appearances t WHERE t.match_id=m.match_id))::INTEGER) with_appearances, "
        "sum((m.finished AND EXISTS (SELECT 1 FROM shots t WHERE t.match_id=m.match_id))::INTEGER) with_shots, "
        "sum((m.finished AND EXISTS (SELECT 1 FROM match_odds t WHERE t.match_id=m.match_id))::INTEGER) with_odds, "
        "sum((NOT m.finished AND EXISTS (SELECT 1 FROM match_odds t WHERE t.match_id=m.match_id))::INTEGER) unfinished_with_odds "
        "FROM matches m GROUP BY ALL ORDER BY 1, 2"
    ),
    "stat_coverage_per_stat": rows(
        "SELECT stat, count(*) n_rows, count(DISTINCT player_id) players, "
        "round(count(*)::DOUBLE / (SELECT count(*) FROM appearances WHERE minutes > 0), 3) share_of_played_appearances "
        "FROM player_match_stats GROUP BY 1 ORDER BY 1"
    ),
    "stat_coverage_by_position": rows(
        "SELECT a.position, s.stat, count(*) n_rows, round(count(*)::DOUBLE / (SELECT count(*) FROM appearances b WHERE b.position = a.position AND b.minutes > 0), 3) AS share_of_played "
        "FROM player_match_stats s JOIN appearances a USING (match_id, player_id) WHERE s.stat IN ('xG','xA','npxG','xGOT','rating','saves','shots') "
        "GROUP BY 1, 2 ORDER BY 1, 2"
    ),
    "fotmob_link_coverage_by_position": rows(
        "WITH played AS (SELECT p.player_id, g.position FROM (SELECT DISTINCT fpl_id, position FROM fpl_players_gw) g JOIN player_ids p ON p.source='fpl' AND p.source_id=g.fpl_id::VARCHAR "
        "WHERE g.fpl_id IN (SELECT fpl_id FROM fpl_points_gw GROUP BY 1 HAVING sum(minutes) > 0)) "
        "SELECT position, count(*) played_players, sum((player_id IN (SELECT player_id FROM player_ids WHERE source='fotmob'))::INTEGER) linked FROM played GROUP BY 1 ORDER BY 1"
    ),
    "fotmob_link_coverage_by_club": rows(
        "WITH played AS (SELECT p.player_id, g.team_id FROM (SELECT * FROM fpl_players_gw QUALIFY snapshot_at = max(snapshot_at) OVER (PARTITION BY fpl_id)) g "
        "JOIN player_ids p ON p.source='fpl' AND p.source_id=g.fpl_id::VARCHAR WHERE g.fpl_id IN (SELECT fpl_id FROM fpl_points_gw GROUP BY 1 HAVING sum(minutes) > 0)) "
        "SELECT t.key club, count(*) played_players, sum((player_id IN (SELECT player_id FROM player_ids WHERE source='fotmob'))::INTEGER) linked "
        "FROM played JOIN teams t USING (team_id) GROUP BY 1 ORDER BY 1"
    ),
    "link_refusal_reasons": rows(
        "SELECT CASE WHEN detail LIKE 'position%' THEN 'position disagrees' ELSE detail END reason, count(*) n FROM ingest_state WHERE source='link' GROUP BY 1 ORDER BY 2 DESC"
    ),
    "field_ownership_coverage": rows(
        "SELECT (SELECT count(*) FROM field_ownership) n_rows, (SELECT count(DISTINCT fpl_id) FROM fpl_players_gw) players, "
        "(SELECT min(sample) FROM field_ownership) sample_min, (SELECT max(sample) FROM field_ownership) sample_max, "
        "(SELECT count(*) FROM field_ownership WHERE eo < owned) eo_below_owned"
    ),
    "freshness": rows(
        "SELECT 'fpl_players_gw' obj, max(snapshot_at)::VARCHAR latest FROM fpl_players_gw UNION ALL "
        "SELECT 'fpl_points_gw', max(ingested_at)::VARCHAR FROM fpl_points_gw UNION ALL "
        "SELECT 'matches', max(ingested_at)::VARCHAR FROM matches UNION ALL "
        "SELECT 'appearances', max(ingested_at)::VARCHAR FROM appearances UNION ALL "
        "SELECT 'match_odds', max(ingested_at)::VARCHAR FROM match_odds UNION ALL "
        "SELECT 'projections', max(created_at)::VARCHAR FROM projections UNION ALL "
        "SELECT 'field_ownership', max(fetched_at)::VARCHAR FROM field_ownership UNION ALL "
        "SELECT 'news_evidence', max(observed_at)::VARCHAR FROM news_evidence UNION ALL "
        "SELECT 'ingest_log', max(run_at)::VARCHAR FROM ingest_log"
    ),
    "stale_unfinished_matches": rows(
        "SELECT competition_id comp, count(*) n, min(kickoff)::VARCHAR first_kickoff FROM matches WHERE NOT finished AND kickoff < now() - INTERVAL 4 HOUR GROUP BY 1"
    ),
    "ingestion_time_vs_event_time": rows(
        "SELECT 'appearances' tbl, min(a.ingested_at)::VARCHAR first_ingest, max(a.ingested_at)::VARCHAR last_ingest, "
        "min(m.kickoff)::VARCHAR first_kickoff, max(m.kickoff)::VARCHAR last_kickoff, "
        "round(avg(epoch(a.ingested_at - m.kickoff))/3600, 1) mean_lag_hours FROM appearances a JOIN matches m USING (match_id)"
    ),
    "projection_runs": rows(
        "SELECT run_id, min(created_at)::VARCHAR created_at, count(DISTINCT gw) gws, count(*) n_rows, "
        "bool_or(run_id IN (SELECT projection_run FROM runs WHERE projection_run IS NOT NULL)) in_runs FROM projections GROUP BY 1 ORDER BY 2"
    ),
    "news_state": rows(
        "SELECT payload->>'state' AS state, payload->>'source' AS src, count(*) AS n FROM news_evidence GROUP BY 1, 2 ORDER BY 3 DESC"
    ),
    "ingest_state_by_source": rows(
        "SELECT source, status, count(*) n FROM ingest_state GROUP BY 1, 2 ORDER BY 1, 2"
    ),
    "tables_never_written": rows(
        "SELECT t FROM (VALUES ('transfers'),('reviews'),('lessons'),('league_translation'),('match_odds_log'),('scout_cache'),('scout_calls')) v(t)"
    ),
}
out = {
    "audit_time_utc": now_utc.isoformat() + "Z",
    "database_sha256_note": "see inventory.json database.sha256 (db unchanged during audit: opened read-only)",
    "summary": summary,
    "existing_quality_checks": existing,
    "checks": [asdict(c) for c in CHECKS],
}
(OUT / "checks.json").write_text(json.dumps(out, indent=1, default=str), encoding="utf-8")
(OUT / "measurements.json").write_text(json.dumps(meas, indent=1, default=str), encoding="utf-8")
print(
    json.dumps(summary),
    "existing quality ran:",
    existing.get("ran"),
    "errors:",
    existing.get("errors"),
)
for c in CHECKS:
    r = c.result
    if r["status"] in ("fail", "error"):
        print(
            r["status"].upper(),
            c.id,
            r.get("violations"),
            "/",
            r.get("denominator"),
            r.get("error", ""),
            r.get("examples", ""),
        )
