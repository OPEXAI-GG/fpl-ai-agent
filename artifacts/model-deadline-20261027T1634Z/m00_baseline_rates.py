from datetime import datetime, timezone
from pathlib import Path
import hashlib

import pandas as pd

from fpl_agent.db import player_links
from fpl_agent.db.database import FootballDB
from fpl_agent.model.features import match_frame, player_gw_frame
from fpl_agent.model.players import fit_player_rates
from fpl_agent.model.translation import Translations


root = Path(__file__).resolve().parent
db = FootballDB(root / "work-data.duckdb", schema="read")
try:
    cutoff = datetime(2026, 10, 27, 16, 34, tzinfo=timezone.utc).replace(tzinfo=None)
    frame = player_gw_frame(db, min_season=2026)
    raw = match_frame(db)
    blocked = player_links.unsupported(db, 2026, cutoff)
    pm = player_links.resolve(raw[raw.date < pd.Timestamp(cutoff.date())],
                              cutoff.date(), cutoff, blocked)
    current = db.query(
        "SELECT player_id, position FROM fpl_players_gw WHERE season = 2026 "
        "QUALIFY row_number() OVER (PARTITION BY player_id "
        "ORDER BY snapshot_at DESC, gw DESC) = 1"
    )
    positions = dict(zip(current.player_id.astype(int), current.position, strict=True))
    rates = fit_player_rates(pm, frame, positions, 2026,
                             Translations.load(db, as_of=cutoff)).sort_values("player_id")
finally:
    db.close()

csv = rates.to_csv(index=False, float_format="%.12g", na_rep="NA", lineterminator="\n")
out = root / "baseline-rates.csv"
out.write_text(csv, encoding="utf-8", newline="")
print({
    "rows": len(rates),
    "sources": rates.source.value_counts(dropna=False).sort_index().to_dict(),
    "evidence_minutes_total": float(rates.evidence_minutes.sum()),
    "xg_share_mean": float(rates.xg_share.mean()),
    "xa_share_mean": float(rates.xa_share.mean()),
    "sha256": hashlib.sha256(csv.encode("utf-8")).hexdigest(),
})
