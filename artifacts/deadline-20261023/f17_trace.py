"""T04/F-17: trace why projected players fall back to position priors (diagnostic copy only)."""
import sys
from datetime import datetime
import pandas as pd
from fpl_agent.db.database import FootballDB
from fpl_agent.model import features, players, translation
from fpl_agent.model.project import current_pool

db = FootballDB(sys.argv[1])
cutoff = datetime(2026, 10, 23, 17, 30)
pool = current_pool(db, 2026, 8, as_of=cutoff)
pm = features.match_frame(db)
print("pool", len(pool), "pm rows", len(pm), "pm players", pm["player_id"].nunique() if len(pm) else 0)
print(pm.head(3).T if len(pm) else "EMPTY")
print("pm minutes>0", int((pm["minutes"] > 0).sum()) if len(pm) else 0)
print("pool ids in pm", len(set(pool["player_id"]) & set(pm["player_id"])) if len(pm) else 0)
print(pm.isna().mean().round(3).to_dict() if len(pm) else "")
frame = features.player_gw_frame(db)
pos = dict(zip(pool["player_id"], pool["position"]))
r = players.fit_player_rates(pm[pm["date"] < pd.Timestamp(cutoff.date())], frame, pos, 2026,
                             translation.Translations.load(db, as_of=cutoff))
print(r["source"].value_counts().to_dict())
