"""DB02.5: what history exists outside football.duckdb and can it be replayed point-in-time?
Read-only. Writes archives.json. Run from the repo root."""

import json
from pathlib import Path

import duckdb

OUT = Path(__file__).parent
k = duckdb.connect("data_cache/calibration-study-20261020.duckdb", read_only=True)


def rows(sql):
    cur = k.execute(sql)
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, map(str, r), strict=True)) for r in cur.fetchall()]


res = {
    "file": "data_cache/calibration-study-20261020.duckdb",
    "fpl_players_gw_by_season": rows(
        "SELECT season, count(*) n_rows, count(DISTINCT fpl_id) players, "
        "count(DISTINCT gw) gws, count(DISTINCT snapshot_at) distinct_snapshot_at, "
        "sum((chance_next IS NULL)::INTEGER) chance_null, sum((news IS NULL)::INTEGER) news_null, "
        "sum((status IS NULL)::INTEGER) status_null "
        "FROM fpl_players_gw GROUP BY 1 ORDER BY 1"
    ),
    "snapshot_after_first_kickoff_of_gw": rows(
        "SELECT g.season, count(*) n_rows, "
        "sum((g.snapshot_at > f.k)::INTEGER) after_first_kickoff "
        "FROM fpl_players_gw g JOIN (SELECT season, gw, min(kickoff) k FROM fpl_points_gw "
        "GROUP BY 1, 2) f USING (season, gw) WHERE g.season < 2026 GROUP BY 1 ORDER BY 1"
    ),
    "raw_keys_sample": rows(
        "SELECT DISTINCT json_keys(raw)::VARCHAR AS keys FROM fpl_players_gw WHERE season < 2026"
    ),
    "id_sources": rows("SELECT source, count(*) n FROM player_ids GROUP BY 1 ORDER BY 1"),
    "matches_by_season": rows(
        "SELECT competition_id, season, count(*) n FROM matches GROUP BY 1, 2 ORDER BY 1, 2"
    ),
    "note": (
        "history rows (2024, 2025) come from the vaastav archive: snapshot_at equals a fixture "
        "kick-off, availability fields are NULL and raw holds only was_home. They are an "
        "event-time "
        "reconstruction, not captured pre-deadline snapshots."
    ),
}
(OUT / "archives.json").write_text(json.dumps(res, indent=1), encoding="utf-8")
print(json.dumps(res, indent=1)[:1800])
