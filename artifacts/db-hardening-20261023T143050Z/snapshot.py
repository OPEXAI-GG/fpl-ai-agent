"""U00: consistent snapshot of a DuckDB file via a read-only ATTACH, then logical verification.

usage: python snapshot.py SOURCE DEST OUT.json  (DEST must not exist; SOURCE is only read)
"""
import hashlib, json, sys, time
from pathlib import Path
import duckdb

src, dst, out = (Path(a).resolve() for a in sys.argv[1:4])
assert src.exists(), src
if dst.exists() or Path(str(dst) + ".wal").exists():
    sys.exit(f"refusing existing destination {dst}")


def sha(p):
    return hashlib.sha256(p.read_bytes()).hexdigest()


def tables(con, cat):
    return [r[0] for r in con.execute(
        "SELECT table_name FROM duckdb_tables() WHERE database_name = ? ORDER BY 1", [cat]).fetchall()]


def shape(con, cat):
    return con.execute(
        "SELECT table_name, column_index, column_name, data_type FROM duckdb_columns() "
        "WHERE database_name = ? ORDER BY 1, 2", [cat]).fetchall()


def digest(con, cat, t):
    n, h = con.execute(f'SELECT count(*), sum(hash(x))::VARCHAR FROM "{cat}"."{t}" x').fetchone()
    return [n, h]


before = sha(src)
t0 = time.perf_counter()
con = duckdb.connect(":memory:")
con.execute(f"ATTACH '{src.as_posix()}' AS s (READ_ONLY)")
con.execute(f"ATTACH '{dst.as_posix()}' AS d")
con.execute("BEGIN TRANSACTION")
con.execute("COPY FROM DATABASE s TO d")
con.execute("COMMIT")
copy_s = time.perf_counter() - t0
st, dt = tables(con, "s"), tables(con, "d")
diffs = {t: (digest(con, "s", t), digest(con, "d", t)) for t in st}
diffs = {t: v for t, v in diffs.items() if v[0] != v[1]}
res = {
    "source": str(src), "dest": str(dst), "source_sha256_before": before,
    "duckdb": duckdb.__version__, "copy_seconds": round(copy_s, 2),
    "tables": len(st), "same_tables": st == dt, "same_columns": shape(con, "s") == shape(con, "d"),
    "digest_differences": diffs,
    "rows": {t: digest(con, "d", t)[0] for t in dt},
}
con.close()
res["source_sha256_after"] = sha(src)
res["source_unchanged"] = res["source_sha256_after"] == before
res["dest_sha256"] = sha(dst)
res["dest_bytes"] = dst.stat().st_size
res["ok"] = res["same_tables"] and res["same_columns"] and not diffs and res["source_unchanged"]
out.write_text(json.dumps(res, indent=1))
print(json.dumps({k: v for k, v in res.items() if k != "rows"}, indent=1))
sys.exit(0 if res["ok"] else 1)
