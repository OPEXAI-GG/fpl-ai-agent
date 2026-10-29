"""DDL for the football database (DuckDB). Every table carries source + ingested_at so models can
ask "what did we know at this deadline?". Season is the start year as an integer (2026 = 2026/27).
"""

TABLES: dict[str, str] = {
    "competitions": """
        competition_id VARCHAR PRIMARY KEY, name VARCHAR, country VARCHAR, tier INTEGER""",
    "teams": """
        team_id INTEGER PRIMARY KEY, name VARCHAR, key VARCHAR""",
    "team_ids": """
        source VARCHAR, source_id VARCHAR, team_id INTEGER, source_name VARCHAR,
        PRIMARY KEY (source, source_id)""",
    "players": """
        player_id INTEGER PRIMARY KEY, name VARCHAR, birth_date DATE, position VARCHAR""",
    "player_ids": """
        source VARCHAR, source_id VARCHAR, player_id INTEGER, source_name VARCHAR,
        PRIMARY KEY (source, source_id)""",
    "matches": """
        match_id INTEGER PRIMARY KEY, competition_id VARCHAR, season INTEGER, matchday INTEGER,
        kickoff TIMESTAMP, match_date DATE, home_team_id INTEGER, away_team_id INTEGER,
        home_goals INTEGER, away_goals INTEGER, finished BOOLEAN,
        source VARCHAR, ingested_at TIMESTAMP""",
    "match_ids": """
        source VARCHAR, source_id VARCHAR, match_id INTEGER,
        PRIMARY KEY (source, source_id)""",
    "team_match": """
        match_id INTEGER, team_id INTEGER, source VARCHAR, is_home BOOLEAN,
        goals_for INTEGER, goals_against INTEGER, xg_for DOUBLE, xg_against DOUBLE,
        shots INTEGER, shots_on_target INTEGER, corners INTEGER, fouls INTEGER,
        yellows INTEGER, reds INTEGER, ingested_at TIMESTAMP,
        PRIMARY KEY (match_id, team_id, source)""",
    "match_odds": """
        match_id INTEGER, source VARCHAR, bookmaker VARCHAR, closing BOOLEAN,
        home DOUBLE, draw DOUBLE, away DOUBLE, over25 DOUBLE, under25 DOUBLE,
        ah_line DOUBLE, ah_home DOUBLE, ah_away DOUBLE, ingested_at TIMESTAMP,
        PRIMARY KEY (match_id, source, bookmaker, closing)""",
    "appearances": """
        match_id INTEGER, player_id INTEGER, team_id INTEGER, source VARCHAR,
        started BOOLEAN, minutes INTEGER, position VARCHAR, ingested_at TIMESTAMP,
        PRIMARY KEY (match_id, player_id, source)""",
    "player_match_stats": """
        match_id INTEGER, player_id INTEGER, source VARCHAR, stat VARCHAR, value DOUBLE,
        ingested_at TIMESTAMP, PRIMARY KEY (match_id, player_id, source, stat)""",
    "shots": """
        source VARCHAR, shot_id VARCHAR, source_match_id VARCHAR, match_id INTEGER,
        player_id INTEGER, minute INTEGER, xg DOUBLE, result VARCHAR, situation VARCHAR,
        shot_type VARCHAR, x DOUBLE, y DOUBLE, assisted_by VARCHAR, last_action VARCHAR,
        ingested_at TIMESTAMP, PRIMARY KEY (source, shot_id)""",
    "transfers": """
        player_id INTEGER, from_team_id INTEGER, to_team_id INTEGER, date DATE, fee VARCHAR,
        source VARCHAR, ingested_at TIMESTAMP""",
    "fpl_players_gw": """
        season INTEGER, gw INTEGER, snapshot_at TIMESTAMP, fpl_id INTEGER, player_id INTEGER,
        team_id INTEGER, position VARCHAR, now_cost INTEGER, selected_by_percent DOUBLE,
        status VARCHAR, chance_next INTEGER, news VARCHAR, transfers_in_event INTEGER,
        transfers_out_event INTEGER, total_points INTEGER, minutes INTEGER, raw JSON,
        PRIMARY KEY (season, gw, snapshot_at, fpl_id)""",
    "fpl_points_gw": """
        season INTEGER, gw INTEGER, fpl_id INTEGER, player_id INTEGER, fixture_id INTEGER,
        minutes INTEGER, total_points INTEGER, bps INTEGER, bonus INTEGER, stats JSON,
        explain JSON, ingested_at TIMESTAMP, kickoff TIMESTAMP,
        PRIMARY KEY (season, gw, fpl_id, fixture_id)""",
    "projections": """
        run_id VARCHAR, model VARCHAR, created_at TIMESTAMP, season INTEGER, gw INTEGER,
        player_id INTEGER, xpts DOUBLE, detail JSON""",
    "field_ownership": """
        season INTEGER, gw INTEGER, fpl_id INTEGER, owned DOUBLE, eo DOUBLE, sample INTEGER,
        fetched_at TIMESTAMP, PRIMARY KEY (season, gw, fpl_id)""",
    "decisions": """
        season INTEGER, gw INTEGER, created_at TIMESTAMP, mode VARCHAR, payload JSON""",
    "reviews": """
        season INTEGER, gw INTEGER, created_at TIMESTAMP, kind VARCHAR, payload JSON""",
    "ingest_log": """
        source VARCHAR, run_at TIMESTAMP, table_name VARCHAR, rows INTEGER, note VARCHAR""",
}

TABLES["ingest_state"] = """
    source VARCHAR, key VARCHAR, status VARCHAR, detail VARCHAR, updated_at TIMESTAMP,
    PRIMARY KEY (source, key)"""

TABLES["league_translation"] = """
    from_comp VARCHAR, to_comp VARCHAR, stat VARCHAR, factor DOUBLE, n_players INTEGER,
    minutes DOUBLE, updated_at TIMESTAMP, PRIMARY KEY (from_comp, to_comp, stat)"""

# every odds observation, append-only: match_odds keeps only the newest price per bookmaker,
# so a past decision's prices can only be reconstructed from here (prospectively, from now on)
TABLES["match_odds_log"] = """
    match_id INTEGER, source VARCHAR, bookmaker VARCHAR, closing BOOLEAN,
    home DOUBLE, draw DOUBLE, away DOUBLE, over25 DOUBLE, under25 DOUBLE,
    ah_line DOUBLE, ah_home DOUBLE, ah_away DOUBLE, ingested_at TIMESTAMP"""

# the scout's validated answers, reused while nothing that went into them changed
TABLES["scout_cache"] = """
    key VARCHAR PRIMARY KEY, player_id INTEGER, p_play DOUBLE, reason VARCHAR,
    created_at TIMESTAMP, expires_at TIMESTAMP"""

# one row per scout call: counts, usage and outcome (never the key or the raw reply)
TABLES["scout_calls"] = """
    called_at TIMESTAMP, model VARCHAR, prompt_version INTEGER, season INTEGER, gw INTEGER,
    eligible INTEGER, cached INTEGER, sent INTEGER, accepted INTEGER, rejected INTEGER,
    input_bytes INTEGER, input_tokens INTEGER, output_tokens INTEGER, stop_reason VARCHAR,
    latency_ms INTEGER, outcome VARCHAR"""

TABLES["runs"] = """
    run_id VARCHAR PRIMARY KEY, version INTEGER, season INTEGER, gw INTEGER, entry_id INTEGER,
    mode VARCHAR, started_at TIMESTAMP, ended_at TIMESTAMP, status VARCHAR, state VARCHAR,
    exit_code INTEGER, snapshot_at TIMESTAMP, projection_run VARCHAR, decision_at TIMESTAMP,
    stages JSON, reasons JSON, report_key VARCHAR, delivered BOOLEAN, delivered_at TIMESTAMP"""

TABLES["lessons"] = """
    season INTEGER, gw INTEGER, kind VARCHAR, points DOUBLE, what VARCHAR, lesson VARCHAR,
    rule VARCHAR, created_at TIMESTAMP"""

TABLES["news_evidence"] = """
    observation_id VARCHAR PRIMARY KEY, observed_at TIMESTAMP, url VARCHAR, payload JSON"""

# Identity evidence (R1). `player_codes` records a source's stable per-person code (FPL's `code`)
# against the canonical player, so a person is linked across seasons by evidence, not by name.
# `migration_log` is the journal of the reversible relabel of legacy `fpl` identity rows.
TABLES["player_codes"] = """
    source VARCHAR, code VARCHAR, player_id INTEGER, evidence VARCHAR,
    first_season INTEGER, recorded_at TIMESTAMP, PRIMARY KEY (source, code)"""

TABLES["migration_log"] = """
    migration_id VARCHAR, table_name VARCHAR, source_id VARCHAR, canonical_id INTEGER,
    source_name VARCHAR, old_source VARCHAR, new_source VARCHAR, applied_at TIMESTAMP,
    rolled_back_at TIMESTAMP, PRIMARY KEY (migration_id, table_name, source_id)"""

# Before/after images of every row an automatic repair or quarantine removed or rewrote (R2), in
# the same transaction as the change; `quality.rollback_repair` restores one step exactly.
TABLES["repair_log"] = """
    repair_id VARCHAR, seq INTEGER, step VARCHAR, table_name VARCHAR, action VARCHAR,
    image VARCHAR, reason VARCHAR, logged_at TIMESTAMP, rolled_back_at TIMESTAMP,
    PRIMARY KEY (repair_id, seq)"""

# The outcome of every source a data update or backfill attempted, failures included (U05).
# Owned by `pipeline.record_attempts`, which writes it in its own transaction after the data work
# committed or rolled back: recording a failed attempt never changes the data or a source's
# success markers (`ingest_state`), and the data's rollback never loses the record. Operational
# only: a source name, times, rows and a short sanitised error, never a payload, a URL's query
# string, a credential or a team id.
TABLES["source_attempts"] = """
    attempt_id VARCHAR, seq INTEGER, job VARCHAR, source VARCHAR, ok BOOLEAN, critical BOOLEAN,
    rows BIGINT, error VARCHAR, started_at TIMESTAMP, finished_at TIMESTAMP, exit_code INTEGER,
    PRIMARY KEY (attempt_id, seq)"""

# Every observed official FPL deadline, versioned: a row is added only when the deadline FPL
# reports for a gameweek differs from the newest one stored, so `observed_at` says what was
# known when (prospective from 2026-10-23; earlier deadlines were never stored).
TABLES["fpl_deadlines"] = """
    season INTEGER, gw INTEGER, deadline_time TIMESTAMP, observed_at TIMESTAMP,
    PRIMARY KEY (season, gw, observed_at)"""

# Identity links found from appearance evidence (`db.link_candidates`) and applied only on request
# (`db-links --apply`), so source rows keep their own ids: a reader maps `from_player_id` (a
# FotMob-only record) to `to_player_id` (an FPL player) only when `valid_from` is at or before
# its cutoff, and never for a rolled-back batch. `evidence` is the proposal, kept for audit.
TABLES["player_links"] = """
    link_id VARCHAR PRIMARY KEY, batch_id VARCHAR, source VARCHAR, season INTEGER,
    from_player_id INTEGER, to_player_id INTEGER, valid_from TIMESTAMP, evidence VARCHAR,
    recorded_at TIMESTAMP, rolled_back_at TIMESTAMP"""
