"""Read-only: count violations of candidate model-input contracts on the snapshot copies."""
import json, sys
import duckdb
CHECKS = {
  "bad_position": "SELECT count(*) FROM fpl_players_gw WHERE position IS NOT NULL AND position NOT IN ('GKP','DEF','MID','FWD')",
  "null_position": "SELECT count(*) FROM fpl_players_gw WHERE position IS NULL",
  "bad_fpl_minutes": "SELECT count(*) FROM fpl_points_gw WHERE minutes < 0 OR minutes > 130",
  "orphan_fpl_points_player": "SELECT count(*) FROM fpl_points_gw f LEFT JOIN players p USING (player_id) WHERE f.player_id IS NOT NULL AND p.player_id IS NULL",
  "orphan_pms_match": "SELECT count(*) FROM player_match_stats t LEFT JOIN matches m USING (match_id) WHERE m.match_id IS NULL",
  "orphan_shots_match": "SELECT count(*) FROM shots t LEFT JOIN matches m USING (match_id) WHERE t.match_id IS NOT NULL AND m.match_id IS NULL",
  "orphan_odds_match": "SELECT count(*) FROM match_odds t LEFT JOIN matches m USING (match_id) WHERE m.match_id IS NULL",
  "orphan_app_team": "SELECT count(*) FROM appearances t LEFT JOIN teams p USING (team_id) WHERE t.team_id IS NOT NULL AND p.team_id IS NULL",
  "orphan_tm_team": "SELECT count(*) FROM team_match t LEFT JOIN teams p USING (team_id) WHERE p.team_id IS NULL",
  "dup_player_fixture": "SELECT count(*) FROM (SELECT 1 FROM fpl_points_gw WHERE fixture_id <> 0 AND player_id IS NOT NULL GROUP BY season, player_id, fixture_id HAVING count(*) > 1)",
  "dup_player_snapshot": "SELECT count(*) FROM (SELECT 1 FROM fpl_players_gw WHERE player_id IS NOT NULL GROUP BY season, gw, snapshot_at, player_id HAVING count(DISTINCT fpl_id) > 1)",
  "fpl_points_no_kickoff": "SELECT count(*) FROM fpl_points_gw WHERE fixture_id <> 0 AND kickoff IS NULL",
  "negative_stat": "SELECT count(*) FROM player_match_stats WHERE value < 0",
  "stat_names": "SELECT string_agg(DISTINCT stat, ',') FROM player_match_stats",
  "bad_translation": "SELECT count(*) FROM league_translation WHERE factor IS NULL OR factor <= 0 OR NOT isfinite(factor)",
  "bad_ou_odds": "SELECT count(*) FROM match_odds WHERE over25 < 1 OR under25 < 1 OR ah_home < 1 OR ah_away < 1",
  "app_minutes_null": "SELECT count(*) FROM appearances WHERE minutes IS NULL",
  "kickoff_vs_match": "SELECT count(*) FROM matches WHERE kickoff IS NULL AND finished",
  "snapshot_future": "SELECT count(*) FROM fpl_players_gw WHERE snapshot_at > now()::TIMESTAMP + INTERVAL 1 DAY",
}
out = {}
for path in sys.argv[1:]:
    con = duckdb.connect(path, read_only=True); r = {}
    for k, q in CHECKS.items():
        try: r[k] = con.execute(q).fetchone()[0]
        except Exception as e: r[k] = f"ERR {type(e).__name__}: {str(e)[:80]}"
    con.close(); out[path] = r
print(json.dumps(out, indent=1))
