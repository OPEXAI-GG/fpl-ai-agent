"""U03: export a snapshot COPY, restore it into a new DB, compare every table's multiset.

usage: python roundtrip.py SNAPSHOT WORKDIR OUT.json   (SNAPSHOT is copied first, never opened rw)
"""
import json, shutil, sys, time
from pathlib import Path
import duckdb
from fpl_agent.db.database import FootballDB, missing_tables, verify_export

snap, work, out = Path(sys.argv[1]), Path(sys.argv[2]), Path(sys.argv[3])
work.mkdir(parents=True)  # refuses an existing folder
src = work / "source.duckdb"
shutil.copyfile(snap, src)
had = {r[0] for r in duckdb.connect(str(snap), read_only=True).execute(
    "SELECT table_name FROM duckdb_tables()").fetchall()}
res = {"snapshot": str(snap), "tables_in_snapshot": len(had)}
t = time.perf_counter()
db = FootballDB(src)  # adds any declared table the snapshot predates
res["created_by_constructor"] = sorted(set(db.counts()) - had)
db.export_parquet(work / "export")
db.close()
res["export_s"] = round(time.perf_counter() - t, 2)
res["verify"] = verify_export(work / "export")
res["missing_tables"] = missing_tables(work / "export")
t = time.perf_counter()
r = FootballDB(work / "restored.duckdb")
res["tables_loaded"] = r.import_parquet(work / "export", require_manifest=True)
res["reimport_loaded"] = r.import_parquet(work / "export", require_manifest=True)
r.close()
res["restore_s"] = round(time.perf_counter() - t, 2)
con = duckdb.connect()
con.execute(f"ATTACH '{src.as_posix()}' AS a (READ_ONLY)")
con.execute(f"ATTACH '{(work / 'restored.duckdb').as_posix()}' AS b (READ_ONLY)")
diff = {}
for (name,) in con.execute("SELECT table_name FROM duckdb_tables() WHERE database_name='a'").fetchall():
    where = " WHERE source <> 'import'" if name == "ingest_state" else ""
    q = lambda c: f'SELECT * FROM {c}."{name}"{where}'
    n = con.execute(f"SELECT (SELECT count(*) FROM ({q('a')} EXCEPT ALL {q('b')})) + "
                    f"(SELECT count(*) FROM ({q('b')} EXCEPT ALL {q('a')}))").fetchone()[0]
    if n:
        diff[name] = n
res["differences"] = diff
res["ok"] = not res["verify"] and not diff and res["reimport_loaded"] == 0
out.write_text(json.dumps(res, indent=1))
print(json.dumps(res, indent=1))
sys.exit(0 if res["ok"] else 1)
