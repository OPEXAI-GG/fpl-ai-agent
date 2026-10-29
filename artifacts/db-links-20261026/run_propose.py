import json, sys, time
from datetime import datetime
from fpl_agent.db.database import FootballDB
from fpl_agent.db import link_candidates as lc
db = FootballDB(sys.argv[1], schema="read")
cut = datetime.fromisoformat(sys.argv[2])
t = time.perf_counter()
r = lc.history(db, 2026, cut)
r["seconds"] = round(time.perf_counter() - t, 2)
names = dict(db.con.execute("SELECT player_id, name FROM players").fetchall())
for a in r["accepted"] + r["rejected"]:
    a["fpl_name"] = names.get(a["fpl_player_id"])
    if "candidate" in a: a["fotmob_name"] = names.get(a["candidate"])
from collections import Counter
print(json.dumps({"accepted": len(r["accepted"]), "rejected": dict(Counter(x["reason"] for x in r["rejected"])), "seconds": r["seconds"]}, indent=1))
json.dump(r, open(sys.argv[3], "w", encoding="utf-8"), indent=1, ensure_ascii=False, default=str)
