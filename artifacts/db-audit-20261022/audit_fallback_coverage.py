# ruff: noqa: E501
"""F-05 follow-up: what do FPL players WITHOUT a FotMob id actually get? Traces (a) FPL's own
underlying stats and (b) the rate source recorded in the latest projection run. Read-only.
Writes fallback_coverage.json. Run from the repo root."""

import json
from pathlib import Path

import duckdb

con = duckdb.connect("data_cache/football.duckdb", read_only=True)


def rows(sql):
    cur = con.execute(sql)
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, map(str, r), strict=True)) for r in cur.fetchall()]


latest = con.execute("SELECT run_id FROM projections ORDER BY created_at DESC LIMIT 1").fetchone()[
    0
]
linked = "p.player_id IN (SELECT player_id FROM player_ids WHERE source='fotmob')"
res = {
    "latest_projection_run": latest,
    "fpl_own_underlying_stats_on_played_rows": rows(
        "SELECT CASE WHEN " + linked + " THEN 'linked to FotMob' ELSE 'not linked' END grp, "
        "count(*) played_player_fixtures, "
        "sum((json_extract(x.stats,'$.expected_goals') IS NOT NULL)::INTEGER) with_fpl_xg, "
        "sum((json_extract(x.stats,'$.defensive_contribution') IS NOT NULL)::INTEGER) with_fpl_defcon "
        "FROM fpl_points_gw x JOIN player_ids p ON p.source='fpl' AND p.source_id = x.fpl_id::VARCHAR "
        "WHERE x.minutes > 0 GROUP BY 1 ORDER BY 1"
    ),
    "projection_rate_source_gw8": rows(
        "SELECT CASE WHEN " + linked + " THEN 'linked to FotMob' ELSE 'not linked' END grp, "
        "coalesce(pr.detail->>'rate_source', '(none recorded)') rate_source, count(*) players, "
        "round(avg(pr.xpts), 2) mean_xpts "
        "FROM projections pr JOIN players p USING (player_id) "
        f"WHERE pr.run_id = '{latest}' AND pr.gw = 8 GROUP BY 1, 2 ORDER BY 1, 2"
    ),
    "projection_rate_source_gw8_by_position_minutes": rows(
        "SELECT p.position, CASE WHEN " + linked + " THEN 'linked' ELSE 'not linked' END grp, "
        "coalesce(pr.detail->>'rate_source', '(none)') rate_source, count(*) players "
        "FROM projections pr JOIN players p USING (player_id) "
        f"WHERE pr.run_id = '{latest}' AND pr.gw = 8 AND (pr.detail->>'exp_minutes')::DOUBLE >= 45 "
        "GROUP BY 1, 2, 3 ORDER BY 1, 2, 3"
    ),
    "note": "rate_source is whatever the projector recorded; no inference about its quality is made here",
}
Path(__file__).with_name("fallback_coverage.json").write_text(
    json.dumps(res, indent=1), encoding="utf-8"
)
print(json.dumps(res, indent=1))
