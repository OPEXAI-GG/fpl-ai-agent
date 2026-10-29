"""U08: snapshot -> migrate -> refresh -> export -> restore -> rollback, all on DISPOSABLE copies.

usage: python chain.py PROD_SNAPSHOT WORKDIR OUT.json
The refresh is the weekly one (bounded public FPL/football-data/Understat requests) with
--no-repairs. Production is never opened; every database here is a new file under WORKDIR.
"""

import hashlib
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import duckdb

from fpl_agent.db import fpl_identity as fi
from fpl_agent.db.database import FootballDB, verify_export

snap, work, out = Path(sys.argv[1]), Path(sys.argv[2]), Path(sys.argv[3])
work.mkdir(parents=True)  # refuses an existing folder
assert "data_cache" not in work.resolve().parts
res: dict = {"snapshot": str(snap)}
t0 = time.perf_counter()


def step(name, fn):
    t = time.perf_counter()
    try:
        res[name] = fn()
    except Exception as e:  # recorded, the chain stops
        res[name] = {"error": f"{type(e).__name__}: {e}"}
        raise
    finally:
        res.setdefault("seconds", {})[name] = round(time.perf_counter() - t, 2)


def sha(p):
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def logical(path, skip=()):
    con = duckdb.connect(str(path), read_only=True)
    try:
        names = [r[0] for r in con.execute("SELECT table_name FROM duckdb_tables()").fetchall()]
        return {
            n: con.execute(f'SELECT count(*), sum(hash(t))::VARCHAR FROM "{n}" t').fetchone()
            for n in names
            if n not in skip
        }
    finally:
        con.close()


def cli(*args):
    env = {**os.environ, "AGENT_MODE": "dry", "PYTHONIOENCODING": "utf-8"}
    p = subprocess.run(
        [sys.executable, "-m", "fpl_agent.cli", *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=env,
        timeout=1800,
    )
    return {"exit": p.returncode, "stdout_tail": p.stdout[-1500:], "stderr_tail": p.stderr[-800:]}


db_path = work / "chain.duckdb"
db_path.write_bytes(snap.read_bytes())
res["copy_sha256"] = sha(db_path)
pre = logical(db_path)


def migrate():
    db = FootballDB(db_path)
    try:
        plan = fi.preflight(db.con)
        return {
            "created_tables": db.created_tables,
            "preflight_problems": plan.problems,
            "migrate": fi.migrate(db.con),
            "noop": fi.migrate(db.con),
            "seed": fi.FplIdentity(db, 2026).seed_codes(),
        }
    finally:
        db.close()


step("migrate", migrate)
migrated = logical(db_path)
step(
    "refresh",
    lambda: cli(
        "db-update",
        "--db",
        str(db_path),
        "--season",
        "2026",
        "--points",
        "--no-repairs",
        "--football-data",
        "E0",
        "--understat-leagues",
        "EPL",
    ),
)
refreshed = logical(db_path)
res["refresh_changed_tables"] = sorted(t for t in refreshed if refreshed[t] != migrated.get(t))


def export():
    db = FootballDB(db_path)
    try:
        return {
            "counts_total": sum(db.export_parquet(work / "export").values()),
            "verify": verify_export(work / "export"),
        }
    finally:
        db.close()


step("export", export)


def restore():
    r = FootballDB(work / "restored.duckdb")
    try:
        return {"tables_loaded": r.import_parquet(work / "export", require_manifest=True)}
    finally:
        r.close()


step("restore", restore)
a = logical(db_path)
b = logical(work / "restored.duckdb")
res["restore_differences"] = sorted(t for t in a if t != "ingest_state" and a[t] != b.get(t))


def rollback():
    db = FootballDB(work / "restored.duckdb")
    try:
        return fi.rollback(db.con)
    finally:
        db.close()


step("rollback", rollback)
after = logical(work / "restored.duckdb")
# the rollback undoes the relabel only; the refresh's new rows legitimately remain
res["identity_labels_after_rollback"] = (
    duckdb.connect(str(work / "restored.duckdb"), read_only=True)
    .execute(
        "SELECT source, count(*) FROM player_ids WHERE source LIKE 'fpl%' GROUP BY 1 ORDER BY 1"
    )
    .fetchall()
)
res["pre_tables"], res["after_tables"] = len(pre), len(after)
res["snapshot_unchanged"] = (
    sha(snap) == json.loads((snap.parent / "prod-snapshot.json").read_text())["dest_sha256"]
)
res["total_seconds"] = round(time.perf_counter() - t0, 1)
res["ok"] = (
    res["refresh"]["exit"] == 0
    and not res["export"]["verify"]
    and not res["restore_differences"]
    and res["snapshot_unchanged"]
)
out.write_text(json.dumps(res, indent=1, default=str))
print(json.dumps({k: v for k, v in res.items() if k not in ("refresh",)}, indent=1, default=str))
print("refresh exit", res["refresh"]["exit"], res["refresh"]["stdout_tail"][-600:])
sys.exit(0 if res["ok"] else 1)
