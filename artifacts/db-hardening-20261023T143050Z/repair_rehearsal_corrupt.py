"""U02: INJECTED corruption (expected repairs) on a disposable copy of real data, then exact rollback.

usage: python repair_rehearsal_corrupt.py SNAPSHOT NEWCOPY OUT.json
"""
import json, shutil, sys, time
from pathlib import Path
from fpl_agent.db.database import FootballDB
from fpl_agent.db import quality as q

snap, copy, out = Path(sys.argv[1]), Path(sys.argv[2]), Path(sys.argv[3])
assert "data_cache" not in copy.resolve().parts and not copy.exists()
shutil.copyfile(snap, copy)
db = FootballDB(copy)
data = lambda: {t: d for t, d in q.table_digests(db).items() if t != "repair_log"}
c = db.con
# injected: one finished EPL match made a club-vs-itself, one shot with xg 2, one price row 7,
# and an existing club duplicated under an old alias with one of its matches pointed at the copy
mid = c.execute("SELECT min(match_id) FROM matches WHERE competition_id='ENG1' AND finished").fetchone()[0]
c.execute("UPDATE matches SET away_team_id = home_team_id WHERE match_id = ?", [mid])
c.execute("UPDATE shots SET xg = 2 WHERE shot_id = (SELECT min(shot_id) FROM shots)")
c.execute("UPDATE fpl_players_gw SET now_cost = 7 WHERE rowid = (SELECT min(rowid) FROM fpl_players_gw)")
tid, name = c.execute("SELECT team_id, name FROM teams WHERE key = 'peterborough'").fetchone() or (None, None)
injected = {"self_match": mid, "split_team": None}
if tid:
    dup = c.execute("SELECT max(team_id) + 1 FROM teams").fetchone()[0]
    c.execute("INSERT INTO teams VALUES (?, 'Peterboro', 'peterboro')", [dup])
    m2 = c.execute("SELECT min(match_id) FROM matches WHERE away_team_id = ?", [tid]).fetchone()[0]
    c.execute("UPDATE matches SET away_team_id = ? WHERE match_id = ?", [dup, m2])
    c.execute("UPDATE team_match SET team_id = ? WHERE match_id = ? AND team_id = ?", [dup, m2, tid])
    injected["split_team"] = [tid, dup, m2]
res = {"snapshot": str(snap), "injected": injected}
before = q.table_digests(db)
t = time.perf_counter()
res["dry"] = {n: [i.detail for i in q.blocked_writes(db, f, n)]
              for n, f in (("repair", q.repair), ("quarantine", q.quarantine))}
res["dry_s"] = round(time.perf_counter() - t, 2)
res["dry_wrote_nothing"] = q.table_digests(db) == before
base = data()
t = time.perf_counter()
res["applied"] = [f"{i.check}: {i.detail[:200]}" for i in q.repair(db) + q.quarantine(db)]
res["apply_s"] = round(time.perf_counter() - t, 2)
res["changed_tables"] = sorted(tb for tb, d in data().items() if d != base.get(tb))
res["journal"] = db.con.execute("SELECT step, table_name, action, count(*) FROM repair_log "
                                "GROUP BY ALL ORDER BY ALL").fetchall()
ids = [r[0] for r in db.con.execute(
    "SELECT DISTINCT repair_id FROM repair_log WHERE rolled_back_at IS NULL ORDER BY 1 DESC").fetchall()]
t = time.perf_counter()
res["rolled_back"] = {i: q.rollback_repair(db, i) for i in ids}
res["rollback_s"] = round(time.perf_counter() - t, 2)
res["rollback_exact"] = data() == base
db.close()
res["ok"] = res["dry_wrote_nothing"] and res["rollback_exact"] and bool(res["applied"])
out.write_text(json.dumps(res, indent=1, default=str))
print(json.dumps(res, indent=1, default=str)[:4000])
sys.exit(0 if res["ok"] else 1)
