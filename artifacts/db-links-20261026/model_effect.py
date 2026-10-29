"""Projection with and without the applied links, on copies only: GW8 (links valid) and a past
cutoff (links not yet valid). Usage: model_effect.py <linked copy> <original copy> <batch> <out>"""
import json, shutil, sys
from datetime import datetime
from fpl_agent.db.database import FootballDB
from fpl_agent.db import player_links
from fpl_agent.model import project
from fpl_agent.model.simulate import SimConfig

linked, orig, batch, out = sys.argv[1:5]
CUT_NOW = datetime(2026, 10, 24, 10)   # before GW8's first kickoff
CUT_PAST = datetime(2026, 10, 20, 20)  # after the first snapshot, before links are valid (10-21)

def proj(path, cutoff, gw):
    db = FootballDB(path, schema="read")
    try:
        p = project.build_projection(db, 2026, [gw], cutoff=cutoff, cfg=SimConfig(n=400), pool_gw=8)
        t = p.meta.copy()
        t["xpts"] = p.points.mean(axis=2).sum(axis=1)
    finally:
        db.close()
    return t

res = {}
for label, cutoff, gw in (("gw8", CUT_NOW, 8), ("past", CUT_PAST, 8)):
    a = proj(linked, cutoff, gw)
    b = proj(orig, cutoff, gw)
    cols = [c for c in ("player_id", "xpts", "rate_source", "evidence_minutes") if c in a.columns]
    m = a[cols].merge(b[cols], on="player_id", suffixes=("_link", "_orig"))
    diff = m[(m["xpts_link"] - m["xpts_orig"]).abs() > 1e-9]
    res[label] = {"players": len(m), "changed": len(diff),
                  "rate_source_link": a["rate_source"].value_counts().to_dict() if "rate_source" in a else None,
                  "rate_source_orig": b["rate_source"].value_counts().to_dict() if "rate_source" in b else None,
                  "sample": diff.head(15).to_dict("records")}
# exact undo on a third copy
undo_copy = linked.replace(".duckdb", "-undo.duckdb")
shutil.copy(linked, undo_copy)
db = FootballDB(undo_copy)
res["undone_links"] = player_links.undo(db, batch)
db.close()
a = proj(undo_copy, CUT_NOW, 8); b = proj(orig, CUT_NOW, 8)
cols = ["player_id", "xpts"]
m = a[cols].merge(b[cols], on="player_id", suffixes=("_u", "_o"), how="outer")
res["after_undo_identical_to_original"] = bool(len(a) == len(b) and ((m["xpts_u"] - m["xpts_o"]).abs() < 1e-12).all())
json.dump(res, open(out, "w", encoding="utf-8"), indent=1, default=str)
print(json.dumps({k: (v if not isinstance(v, dict) else {kk: vv for kk, vv in v.items() if kk != "sample"}) for k, v in res.items()}, indent=1, default=str))
