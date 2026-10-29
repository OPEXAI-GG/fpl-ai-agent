"""U07 bounded synthetic growth: fpl_points_gw/fpl_players_gw replicated into 9 fake past
seasons (10x rows) on a disposable copy; time the feature path. Labelled synthetic.

usage: python perf_growth.py PERFCOPY OUT.json
"""
import json, statistics, sys, time
from fpl_agent.db.database import FootballDB
from fpl_agent.model import features as F

db = FootballDB(sys.argv[1])
for k in range(1, 10):
    for t in ("fpl_points_gw", "fpl_players_gw"):
        db.con.execute(f"INSERT INTO {t} BY NAME SELECT * REPLACE (season - {k} * 100 AS season) "
                       f"FROM {t} WHERE season = 2026")
rows = db.con.execute("SELECT count(*) FROM fpl_points_gw").fetchone()[0]
res = {"synthetic": True, "fpl_points_gw_rows": rows}
for name, fn in {"player_gw_frame": lambda: F.player_gw_frame(db),
                 "minutes_features": lambda: F.minutes_features(F.player_gw_frame(db))}.items():
    t = []
    for _ in range(4):
        s = time.perf_counter(); fn(); t.append(time.perf_counter() - s)
    res[name] = {"cold_s": round(t[0], 3), "warm_median_s": round(statistics.median(t[1:]), 3)}
db.close()
print(json.dumps(res, indent=1)); open(sys.argv[2], "w").write(json.dumps(res, indent=1))
