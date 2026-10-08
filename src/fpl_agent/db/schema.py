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
