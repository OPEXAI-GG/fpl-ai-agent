"""T03: post-refresh integrity, squad identity coverage and per-input freshness (read only)."""
import json, sys, urllib.request
from datetime import datetime, timezone
from pathlib import Path
import duckdb
from fpl_agent.db.fpl_identity import season_map

W = Path(sys.argv[1]); ENTRY = 0  # owner's FPL entry id redacted
UA = {"User-Agent": "fpl-agent-dry/1.0"}
def get(url, name):
    body = urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=60).read()
    (W / "inputs" / name).write_bytes(body); return json.loads(body)
entry = get(f"https://fantasy.premierleague.com/api/entry/{ENTRY}/", "entry.json")
cur = entry["current_event"]
picks = get(f"https://fantasy.premierleague.com/api/entry/{ENTRY}/event/{cur}/picks/", f"picks_gw{cur}.json")
hist = get(f"https://fantasy.premierleague.com/api/entry/{ENTRY}/history/", "history.json")
xfers = get(f"https://fantasy.premierleague.com/api/entry/{ENTRY}/transfers/", "transfers.json")
con = duckdb.connect(str(W / "work.duckdb"), read_only=True)
q = lambda s, p=None: con.execute(s, p or []).fetchall()
canon = season_map(con, "player", 2026)
owned = [p["element"] for p in picks["picks"]]
out = {"checked_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
       "entry": {"current_event": cur, "bank": picks["entry_history"]["bank"], "value": picks["entry_history"]["value"],
                 "event_transfers": picks["entry_history"]["event_transfers"], "active_chip": picks["active_chip"],
                 "chips_used": [(c["name"], c["event"]) for c in hist["chips"]], "n_transfers_total": len(xfers)},
       "squad_identity": {"owned": owned, "missing_canonical": [e for e in owned if e not in canon]},
       "bootstrap_ids_without_canonical": len(set(json.loads((W/"inputs"/"bootstrap.json").read_bytes()) and
           [e["id"] for e in json.loads((W/"inputs"/"bootstrap.json").read_bytes())["elements"]]) - set(canon)),
       "duplicates": {
         "player_ids_key": q("SELECT count(*) FROM (SELECT source, source_id FROM player_ids GROUP BY 1,2 HAVING count(*)>1)")[0][0],
         "canonical_with_two_fpl_ids_2026": q("SELECT count(*) FROM (SELECT player_id FROM player_ids WHERE source='fpl-2026' GROUP BY 1 HAVING count(*)>1)")[0][0],
         "player_codes_multi_code_players": q("SELECT count(*) FROM (SELECT player_id FROM player_codes GROUP BY 1 HAVING count(*)>1)")[0][0],
         "latest_snapshot_dupe_rows": q("SELECT count(*) FROM (SELECT gw, snapshot_at, fpl_id FROM fpl_players_gw GROUP BY ALL HAVING count(*)>1)")[0][0],
         "points_dupe_rows": q("SELECT count(*) FROM (SELECT season, gw, fpl_id, fixture_id FROM fpl_points_gw GROUP BY ALL HAVING count(*)>1)")[0][0]},
       "orphans": {
         "player_ids->players": q("SELECT count(*) FROM player_ids i LEFT JOIN players p USING (player_id) WHERE p.player_id IS NULL")[0][0],
         "fpl_players_gw->players": q("SELECT count(*) FROM fpl_players_gw g LEFT JOIN players p USING (player_id) WHERE g.player_id IS NOT NULL AND p.player_id IS NULL")[0][0],
         "fpl_points_gw->players": q("SELECT count(*) FROM fpl_points_gw g LEFT JOIN players p USING (player_id) WHERE g.player_id IS NOT NULL AND p.player_id IS NULL")[0][0],
         "match_ids->matches": q("SELECT count(*) FROM match_ids i LEFT JOIN matches m USING (match_id) WHERE m.match_id IS NULL")[0][0],
         "team_ids->teams": q("SELECT count(*) FROM team_ids i LEFT JOIN teams t USING (team_id) WHERE t.team_id IS NULL")[0][0]},
       "mandatory_nulls": {
         "latest_snapshot_null_player_id": q("SELECT count(*) FROM fpl_players_gw WHERE season=2026 AND gw=8 AND player_id IS NULL")[0][0],
         "latest_snapshot_null_cost": q("SELECT count(*) FROM fpl_players_gw WHERE season=2026 AND gw=8 AND now_cost IS NULL")[0][0],
         "gw8_fixtures_null_kickoff": q("SELECT count(*) FROM matches m JOIN match_ids i USING (match_id) WHERE i.source='fpl-2026' AND m.matchday=8 AND m.kickoff IS NULL")[0][0]},
       "freshness": {
         "fpl_players_gw_latest": q("SELECT gw, max(snapshot_at), count(DISTINCT fpl_id) FROM fpl_players_gw WHERE season=2026 GROUP BY gw ORDER BY gw DESC LIMIT 2"),
         "fpl_points_gw_by_gw": q("SELECT gw, count(*), max(ingested_at) FROM fpl_points_gw WHERE season=2026 GROUP BY gw ORDER BY gw"),
         "ingest_log_latest": q("SELECT source, table_name, max(at) FROM ingest_log GROUP BY 1,2 ORDER BY 3 DESC LIMIT 12") if q("SELECT count(*) FROM duckdb_columns() WHERE table_name='ingest_log' AND column_name='at'")[0][0] else q("SELECT * FROM ingest_log ORDER BY 1 DESC LIMIT 0")},
       "link_flags": q("SELECT key, detail FROM ingest_state WHERE source='link' LIMIT 20"),
}
print(json.dumps(out, indent=1, default=str))
(W / "refresh_checks.json").write_text(json.dumps(out, indent=1, default=str))
