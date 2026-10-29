"""U02: dry impact report, apply and exact rollback of repair+quarantine on a DISPOSABLE copy.

usage: python repair_rehearsal.py SNAPSHOT NEWCOPY OUT.json
"""
import json, shutil, sys
from pathlib import Path
from fpl_agent.db.database import FootballDB
from fpl_agent.db import quality as q

snap, copy, out = Path(sys.argv[1]), Path(sys.argv[2]), Path(sys.argv[3])
assert "data_cache" not in copy.resolve().parts and not copy.exists()
shutil.copyfile(snap, copy)
db = FootballDB(copy)
data = lambda: {t: d for t, d in q.table_digests(db).items() if t != "repair_log"}
res = {"snapshot": str(snap), "copy": str(copy)}
before = q.table_digests(db)
res["dry"] = {n: [i.detail for i in q.blocked_writes(db, f, n)]
              for n, f in (("repair", q.repair), ("quarantine", q.quarantine))}
res["dry_wrote_nothing"] = q.table_digests(db) == before
base = data()
res["applied"] = [f"{i.check}: {i.detail}" for i in q.repair(db) + q.quarantine(db)]
res["changed_tables"] = sorted(t for t, d in data().items() if d != base.get(t))
ids = [r[0] for r in db.con.execute(
    "SELECT DISTINCT repair_id FROM repair_log WHERE rolled_back_at IS NULL ORDER BY 1 DESC").fetchall()]
res["journal"] = db.con.execute("SELECT repair_id, table_name, action, count(*) FROM repair_log "
                                "GROUP BY ALL ORDER BY ALL").fetchall()
res["rolled_back"] = {i: q.rollback_repair(db, i) for i in ids}
res["rollback_exact"] = data() == base
db.close()
res["ok"] = res["dry_wrote_nothing"] and res["rollback_exact"]
out.write_text(json.dumps(res, indent=1, default=str))
print(json.dumps(res, indent=1, default=str)[:3000])
sys.exit(0 if res["ok"] else 1)
