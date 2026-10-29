"""Projections with and without the applied links (copies only), under the corrected rules: a link
counts only once written (recorded_at) and supported (valid_from) by the decision time.
Usage: model_effect.py <linked copy> <original copy> <batch> <out>"""
import json, shutil, sys
from datetime import UTC, datetime
from fpl_agent.db.database import FootballDB
from fpl_agent.db import player_links
from fpl_agent.db.eligibility import excluded_fixtures
from fpl_agent.db.quality import source_conflicts
from fpl_agent.db.link_candidates import revalidate
from fpl_agent.model import project
from fpl_agent.model.simulate import SimConfig

linked, orig, batch, out = sys.argv[1:5]
NOW = datetime.now(UTC).replace(tzinfo=None)
CASES = {"past_2026-10-20T20": (datetime(2026, 10, 20, 20), 8),   # before valid_from
         "gw8_2026-10-24T10": (datetime(2026, 10, 24, 10), 8),    # valid, but before recorded_at
         "gw9_now": (NOW, 9)}                                     # written and valid

def proj(path, cutoff, gw):
    db = FootballDB(path, schema="read")
    try:
        p = project.build_projection(db, 2026, [gw], cutoff=cutoff, cfg=SimConfig(n=400), pool_gw=8)
    finally:
        db.close()
    t = p.meta.copy(); t["xpts"] = p.points.mean(axis=2).sum(axis=1)
    return t

res = {"now": NOW.isoformat()}
for label, (cutoff, gw) in CASES.items():
    a, b = proj(linked, cutoff, gw), proj(orig, cutoff, gw)
    m = a[["player_id", "xpts", "rate_source"]].merge(b[["player_id", "xpts", "rate_source"]], on="player_id", suffixes=("_link", "_orig"))
    res[label] = {"players": len(m), "xpts_changed": int(((m.xpts_link - m.xpts_orig).abs() > 1e-9).sum()),
                  "rate_source_link": a.rate_source.value_counts().to_dict(),
                  "rate_source_orig": b.rate_source.value_counts().to_dict()}
db = FootballDB(linked, schema="read")
res["revalidate_now"] = revalidate(db, 2026)
c = source_conflicts(db)
res["conflicts_linked_copy"] = len(c)
res["conflicts_via_link"] = [x for x in c if x["linked_record"] is not None]
res["excluded_matches_linked_copy"] = len(excluded_fixtures(db)[0])
db.close()
db = FootballDB(orig, schema="read")
res["conflicts_original"] = len(source_conflicts(db)); res["excluded_matches_original"] = len(excluded_fixtures(db)[0])
db.close()
undo_copy = linked.replace(".duckdb", "-undo.duckdb"); shutil.copy(linked, undo_copy)
db = FootballDB(undo_copy); res["undone_links"] = player_links.undo(db, batch); db.close()
a, b = proj(undo_copy, NOW, 9), proj(orig, NOW, 9)
m = a[["player_id", "xpts"]].merge(b[["player_id", "xpts"]], on="player_id", how="outer", suffixes=("_u", "_o"))
res["gw9_after_undo_identical_to_original"] = bool(len(a) == len(b) and ((m.xpts_u - m.xpts_o).abs() < 1e-12).all())
json.dump(res, open(out, "w", encoding="utf-8"), indent=1, default=str)
print(json.dumps({k: v for k, v in res.items() if k != "conflicts_via_link"}, indent=1, default=str)); print("via link:", len(res["conflicts_via_link"]))
