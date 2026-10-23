"""U07: fixed workloads on a disposable copy of the work snapshot; 1 cold + 3 warm repetitions.

usage: python perf.py SNAPSHOT NEWCOPY OUT.json
"""
import json, platform, shutil, statistics, sys, time, tracemalloc
from pathlib import Path
from fpl_agent.db.database import FootballDB
from fpl_agent.db import quality
from fpl_agent.model import features

snap, copy, out = Path(sys.argv[1]), Path(sys.argv[2]), Path(sys.argv[3])
assert not copy.exists()
shutil.copyfile(snap, copy)
db = FootballDB(copy)
work = {
    "player_gw_frame": lambda: features.player_gw_frame(db),
    "minutes_features": lambda: features.minutes_features(features.player_gw_frame(db)),
    "match_frame": lambda: features.match_frame(db),
    "run_checks": lambda: quality.run_checks(db),
    "blocked_writes(repair+quarantine)": lambda: (quality.blocked_writes(db, quality.repair, "r"),
                                                  quality.blocked_writes(db, quality.quarantine, "q")),
    "export_parquet": lambda: db.export_parquet(copy.parent / (copy.stem + "-export"), keep=0),
}
res = {"python": sys.version.split()[0], "machine": platform.platform(), "processor": platform.processor(),
       "snapshot": str(snap), "workloads": {}}
for name, fn in work.items():
    times = []
    for i in range(4):  # timing without tracing (tracemalloc slows pandas several-fold)
        t = time.perf_counter(); fn(); times.append(time.perf_counter() - t)
    tracemalloc.start()
    fn()  # one separate traced run for the Python-side peak
    peak = tracemalloc.get_traced_memory()[1]
    tracemalloc.stop()
    res["workloads"][name] = {"cold_s": round(times[0], 3), "warm_median_s": round(statistics.median(times[1:]), 3),
                              "py_peak_mb": round(peak / 2**20, 1)}
db.close()
res["db_bytes"] = copy.stat().st_size
out.write_text(json.dumps(res, indent=1))
print(json.dumps(res, indent=1))
