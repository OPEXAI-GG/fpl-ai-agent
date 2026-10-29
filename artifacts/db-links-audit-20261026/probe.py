import sys, duckdb
con = duckdb.connect(sys.argv[1], read_only=True)
q = lambda s, p=[]: con.execute(s, p).fetchall()
print("links", q("SELECT season, count(*), count(*) FILTER (WHERE rolled_back_at IS NULL), min(valid_from), max(valid_from), min(recorded_at), max(recorded_at) FROM player_links GROUP BY 1"))
# 1. FPL vs FotMob conflicts hidden for linked pairs (same rule as quality.source_conflicts)
FFM = "(SELECT DISTINCT m.season, i.source_id, i.match_id FROM match_ids i JOIN matches m USING (match_id) WHERE i.source = 'fpl' OR i.source = 'fpl-' || m.season::VARCHAR)"
rows = q(f"""WITH f AS (SELECT i.match_id, x.season, x.player_id,
  sum(TRY_CAST(json_extract_string(x.stats,'$.goals_scored') AS DOUBLE)) AS goals, sum(x.minutes) AS minutes
  FROM fpl_points_gw x JOIN {FFM} i ON i.season=x.season AND i.source_id=x.fixture_id::VARCHAR
  WHERE x.player_id IS NOT NULL GROUP BY ALL),
 l AS (SELECT from_player_id q, to_player_id p FROM player_links WHERE rolled_back_at IS NULL)
 SELECT f.season, f.match_id, f.player_id, l.q, f.goals, s.value, f.minutes, a.minutes FROM f JOIN l ON l.p=f.player_id
 LEFT JOIN player_match_stats s ON s.match_id=f.match_id AND s.player_id=l.q AND s.stat='goals' AND s.source='fotmob'
 LEFT JOIN appearances a ON a.match_id=f.match_id AND a.player_id=l.q AND a.source='fotmob'
 WHERE f.goals <> s.value OR abs(f.minutes - a.minutes) > 5 ORDER BY ALL""")
print("hidden conflicts on linked pairs:", len(rows)); [print("  ", r) for r in rows[:10]]
# 2. cross-season rows credited by a season-2026 link
print("linked record rows by season:", q("""SELECT m.season, count(*) FROM appearances a JOIN matches m USING (match_id)
 JOIN player_links l ON l.from_player_id=a.player_id AND l.rolled_back_at IS NULL WHERE a.source='fotmob' GROUP BY 1 ORDER BY 1"""))
# 3. evidence recorded after valid_from (ingested_at vs valid_from)
print("links whose FotMob evidence rows were ingested after valid_from:", q("""
 SELECT count(DISTINCT l.link_id), min(a.ingested_at), max(a.ingested_at) FROM player_links l JOIN appearances a
 ON a.player_id=l.from_player_id AND a.source='fotmob' JOIN matches m USING (match_id)
 WHERE l.rolled_back_at IS NULL AND m.season=l.season AND m.kickoff < l.valid_from AND a.ingested_at > l.valid_from"""))
print("FPL points rows ingested after valid_from for linked players:", q("""
 SELECT count(DISTINCT l.link_id) FROM player_links l JOIN fpl_points_gw p ON p.player_id=l.to_player_id AND p.season=l.season
 WHERE l.rolled_back_at IS NULL AND p.kickoff < l.valid_from AND p.ingested_at > l.valid_from"""))
# 4. team evidence from reconstructed snapshots only
print("links whose team evidence before valid_from is only reconstructed snapshots:", q("""
 SELECT count(*) FROM player_links l WHERE l.rolled_back_at IS NULL AND NOT EXISTS (
  SELECT 1 FROM fpl_players_gw s WHERE s.player_id=l.to_player_id AND s.season=l.season AND s.snapshot_at < l.valid_from
  AND json_extract(s.raw,'$.element_type') IS NOT NULL)"""))
# 5. linked player now also holding direct fotmob id / stale
print("linked FPL players that also hold a direct FotMob id:", q("""SELECT count(*) FROM player_links l JOIN player_ids i ON i.player_id=l.to_player_id AND i.source='fotmob' WHERE l.rolled_back_at IS NULL"""))
print("links pointing at missing players:", q("""SELECT count(*) FROM player_links l WHERE NOT EXISTS (SELECT 1 FROM players p WHERE p.player_id=l.to_player_id) OR NOT EXISTS (SELECT 1 FROM players p WHERE p.player_id=l.from_player_id)"""))
