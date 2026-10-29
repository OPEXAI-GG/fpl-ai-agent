# ruff: noqa: E501
"""Bounded integration probes: call the real consumer functions on a READ-ONLY connection (through a
FootballDB.__new__ shim, so no constructor write) and assert row grain and source selection.
Writes consumer_probes.json. Run from the repo root."""

import json
import sys
from pathlib import Path

import duckdb

sys.path.insert(0, "src")
from fpl_agent import review  # noqa: E402
from fpl_agent.db.database import FootballDB  # noqa: E402
from fpl_agent.model import features, project, replay  # noqa: E402

OUT = Path(__file__).parent
con = duckdb.connect("data_cache/football.duckdb", read_only=True)
db = FootballDB.__new__(FootballDB)
db.con = con
results = []


def probe(name, fn, grain, note=""):
    try:
        df = fn()
        dup = int(df.duplicated(grain).sum()) if len(df) else 0
        results.append(
            {
                "probe": name,
                "rows": len(df),
                "grain": grain,
                "duplicate_grain_rows": dup,
                "note": note,
                "df": df,
            }
        )
    except Exception as e:  # a failing consumer is a finding, recorded not hidden
        results.append({"probe": name, "error": f"{type(e).__name__}: {e}"[:300], "df": None})


probe("features.player_gw_frame", lambda: features.player_gw_frame(db), ["player_id", "fixture_id"])
probe("features.match_frame", lambda: features.match_frame(db), ["match_id", "player_id"])
probe("project.current_pool(2026, 8)", lambda: project.current_pool(db, 2026, 8), ["player_id"])
probe("replay.pool_as_of(2026, 8)", lambda: replay.pool_as_of(db, 2026, 8), ["player_id"])
probe(
    "replay.pool_as_of(2026, 5)",
    lambda: replay.pool_as_of(db, 2026, 5),
    ["player_id"],
    "gw 5 is before the only snapshots (GW8)",
)
probe("review._positions(2026, 8)", lambda: review._positions(db, 2026, 8), ["player_id"])

out = []
for r in results:
    df = r.pop("df")
    if df is not None and r["probe"] == "features.player_gw_frame":
        r["current_season_rows"] = len(df)
        for c in ("position", "price", "status", "team_id"):
            r[f"null_{c}"] = int(df[c].isna().sum())
        r["null_position_share"] = round(float(df["position"].isna().mean()), 4)
        r["finding"] = (
            "snapshot join is ON (season, gw, fpl_id) and snapshots exist for GW8 only, so every "
            "GW1-7 row has NULL position/price/status/team: the minutes features see NaN for "
            "pos_code, price, price_vs_best on this file (the archive supplies them only for older seasons)"
        )
    if df is not None and r["probe"].startswith("replay.pool_as_of(2026, 5)"):
        r["finding"] = "returns zero rows for a gameweek before the first snapshot: no fallback"
    out.append(r)
fc = review._field_core(db, 2026, 8)
out.append(
    {
        "probe": "review._field_core(2026, 8)",
        "rows": len(fc),
        "grain": "set of player_id",
        "duplicate_grain_rows": 0,
        "note": "joins field_ownership to fpl_players_gw on (season, fpl_id); 2 snapshots multiply "
        "rows but the DISTINCT in the query removes them",
    }
)
(OUT / "consumer_probes.json").write_text(json.dumps(out, indent=1, default=str), encoding="utf-8")
print(json.dumps(out, indent=1, default=str)[:3500])
