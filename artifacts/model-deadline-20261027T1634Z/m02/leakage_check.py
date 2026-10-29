"""Read-only check: in the exploratory GW5-7 reconstruction, fitting rows end before the target
gameweek starts, so no target or later match result reaches the features or the fit."""
import json
from pathlib import Path

import pandas as pd

from fpl_agent.db.database import FootballDB
from fpl_agent.model.features import player_gw_frame

here = Path(__file__).resolve().parent
db = FootballDB(here.parent / "work-data.duckdb", schema="read")
try:
    frame = player_gw_frame(db, min_season=2026)
finally:
    db.close()
out = {}
for gw in (5, 6, 7):
    cut = frame[(frame.gw == gw) & (frame.minutes > 0)].kickoff.min()  # same rule as m02_compare
    fit = frame[frame.kickoff < cut]
    out[gw] = {
        "cutoff": str(cut),
        "fit_rows": len(fit),
        "fit_gws": sorted(int(g) for g in fit.gw.unique()),
        "target_or_later_rows_in_fit": int((fit.gw >= gw).sum()),
        "latest_fit_kickoff": str(fit.kickoff.max()),
        "hours_from_latest_fit_kickoff_to_cutoff": float(
            (pd.Timestamp(cut) - pd.Timestamp(fit.kickoff.max())).total_seconds() / 3600),
    }
(here / "leakage-check.json").write_text(json.dumps(out, indent=2))
print(json.dumps(out, indent=1))
