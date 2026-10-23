"""DB01: read-only inventory and coverage ledger for data_cache/football.duckdb.

Run from the repo root:  .venv/Scripts/python.exe artifacts/db-audit-20261022/audit_inventory.py
Writes inventory.json and coverage.csv next to this file. Opens the database read-only, never
imports FootballDB (its constructor writes), and records metadata and aggregates only.
"""

from __future__ import annotations

import csv
import hashlib
import json
import platform
import re
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

import duckdb

sys.path.insert(0, str(Path(__file__).parent))
from annotations import COLUMN_NOTES, TABLE_NOTES  # noqa: E402

# Tables declared in schema.TABLES by the R1 change that the production file does not have yet:
# applying them to production is a separate, gated step. They are not drift, and they are not
# part of the 237-column ledger of the live file.
PENDING_APPLY = {
    "player_codes": "R1: stable per-person FPL code -> canonical player (identity evidence)",
    "migration_log": "R1: journal of the reversible fpl -> fpl-<season> relabel",
}

ROOT = Path.cwd()
OUT = Path(__file__).parent
DB = ROOT / "data_cache" / "football.duckdb"
SRC = ROOT / "src" / "fpl_agent"


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def git(*args: str) -> str:
    return subprocess.run(
        ["git", *args], capture_output=True, text=True, check=False
    ).stdout.strip()


def declared() -> dict[str, dict[str, str]]:
    """Columns and types declared in schema.TABLES, parsed from the DDL text."""
    sys.path.insert(0, str(ROOT / "src"))
    from fpl_agent.db.schema import TABLES

    out: dict[str, dict[str, str]] = {}
    for name, ddl in TABLES.items():
        cols: dict[str, str] = {}
        for part in re.split(r",\s*(?![^()]*\))", ddl.strip()):
            part = part.strip()
            if not part or part.upper().startswith("PRIMARY KEY"):
                continue
            tokens = part.split()
            cols[tokens[0]] = tokens[1].upper()
        out[name] = cols
    return out


def code_refs(table: str) -> dict[str, list[str]]:
    """Textual (not proven) writer/reader modules for a table."""
    write = re.compile(
        rf"(INSERT\s+(OR\s+REPLACE\s+)?INTO|UPDATE|DELETE\s+FROM)\s+{table}\b"
        rf"|\.(upsert|append)\(\s*[\"']{table}[\"']",
        re.I,
    )
    read = re.compile(rf"\b(FROM|JOIN)\s+{table}\b", re.I)
    writers, readers = [], []
    for f in sorted(SRC.rglob("*.py")):
        text = f.read_text(encoding="utf-8", errors="replace")
        rel = f.relative_to(ROOT).as_posix()
        if write.search(text):
            writers.append(rel)
        if read.search(text):
            readers.append(rel)
    return {"writers": writers, "readers": readers}


def main() -> int:
    audit_time = datetime.now(UTC).isoformat()
    st = DB.stat()
    con = duckdb.connect(str(DB), read_only=True)
    decl = declared()

    tables = [
        r[0]
        for r in con.execute(
            "SELECT table_name FROM duckdb_tables() WHERE schema_name='main' ORDER BY 1"
        ).fetchall()
    ]
    columns = con.execute(
        "SELECT table_name, column_name, data_type, is_nullable, column_default, column_index "
        "FROM duckdb_columns() WHERE schema_name='main' AND NOT internal "
        "ORDER BY table_name, column_index"
    ).fetchall()
    constraints = con.execute(
        "SELECT table_name, constraint_type, constraint_column_names FROM duckdb_constraints() "
        "WHERE schema_name='main' ORDER BY 1, 2"
    ).fetchall()
    pk = {t: c for t, ty, c in constraints if ty == "PRIMARY KEY"}

    drift: list[str] = []
    pending: list[str] = []
    for t in sorted(set(tables) | set(decl)):
        if t not in decl:
            drift.append(f"{t}: in database but not declared")
        elif t in PENDING_APPLY:
            pending.append(t)
        elif t not in tables:
            drift.append(f"{t}: declared but missing")
    live = {t: {c: ty for tt, c, ty, *_ in columns if tt == t} for t in tables}
    for t in sorted(set(tables) & set(decl)):
        if list(live[t]) != list(decl[t]):
            drift.append(
                f"{t}: column order/set differs: live={list(live[t])} declared={list(decl[t])}"
            )
        for c in set(live[t]) & set(decl[t]):
            lt, dt = live[t][c], decl[t][c]
            if lt.split("(")[0] != dt.split("(")[0] and not (lt == "VARCHAR" and dt == "VARCHAR"):
                drift.append(f"{t}.{c}: live type {lt} vs declared {dt}")

    # --- coverage ledger ------------------------------------------------------------
    rows = []
    unannotated: list[str] = []
    for t in tables:
        n = con.execute(f"SELECT count(*) FROM {t}").fetchone()[0]
        note = TABLE_NOTES.get(t)
        if note is None:
            unannotated.append(f"table {t}")
            note = ("?",) * 8
        refs = code_refs(t)
        for _, c, ty, nullable, default, idx in [x for x in columns if x[0] == t]:
            meaning = COLUMN_NOTES.get(c)
            if meaning is None:
                unannotated.append(f"column {t}.{c}")
                meaning = "?"
            q = f'"{c}"'
            nulls = con.execute(f"SELECT count(*) FROM {t} WHERE {q} IS NULL").fetchone()[0]
            distinct = None
            lo = hi = None
            if ty not in ("JSON",):
                distinct = con.execute(f"SELECT count(DISTINCT {q}) FROM {t}").fetchone()[0]
            if (
                ty in ("INTEGER", "BIGINT", "DOUBLE", "DATE", "TIMESTAMP", "BOOLEAN")
                and n - nulls > 0
            ):
                lo, hi = con.execute(
                    f"SELECT min({q})::VARCHAR, max({q})::VARCHAR FROM {t}"
                ).fetchone()
            rows.append(
                {
                    "table": t,
                    "column": c,
                    "position": idx,
                    "type": ty,
                    "nullable": nullable,
                    "default": default,
                    "in_primary_key": c in (pk.get(t) or []),
                    "meaning_unit": meaning,
                    "table_rows": n,
                    "null_count": nulls,
                    "null_share": round(nulls / n, 4) if n else "",
                    "distinct": distinct if distinct is not None else "",
                    "min": lo if lo is not None else "",
                    "max": hi if hi is not None else "",
                    "row_grain": note[0],
                    "natural_key": note[1],
                    "surrogate_key": note[2],
                    "source": note[3],
                    "update_semantics": note[4],
                    "time_semantics": note[5],
                    "retention": note[6],
                    "sensitivity": note[7],
                    "writers_textual": ";".join(
                        r.removeprefix("src/fpl_agent/") for r in refs["writers"]
                    ),
                    "readers_textual": ";".join(
                        r.removeprefix("src/fpl_agent/") for r in refs["readers"]
                    ),
                    "existing_checks": "",  # filled by audit_ledger.py
                    "audit_outcome": "",  # filled by audit_ledger.py
                }
            )

    ledger_tables = {r["table"] for r in rows}
    ledger_cols = {(r["table"], r["column"]) for r in rows}
    live_cols = {(t, c) for t in tables for c in live[t]}
    reconcile = {
        "live_tables": len(tables),
        "ledger_tables": len(ledger_tables),
        "live_columns": len(live_cols),
        "ledger_columns": len(ledger_cols),
        "missing_from_ledger": sorted(map(str, live_cols - ledger_cols)),
        "unannotated": unannotated,
        "ok": live_cols == ledger_cols and not unannotated and set(tables) == ledger_tables,
    }

    with open(OUT / "coverage.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)

    # --- files outside DuckDB -------------------------------------------------------
    def meta(p: Path) -> dict:
        s = p.stat()
        return {
            "path": p.relative_to(ROOT).as_posix(),
            "bytes": s.st_size,
            "mtime": datetime.fromtimestamp(s.st_mtime, UTC).isoformat(),
        }

    cache = ROOT / "data_cache"
    files = [
        meta(p)
        for p in sorted(cache.rglob("*"))
        if p.is_file()
        and "frozen-verification-env" not in p.parts
        and "uv-verification-cache" not in p.parts
        and "verification-tools" not in p.parts
    ]
    archives = []
    for p in [
        cache / "calibration-study-20261020.duckdb",
        *sorted((cache / "backups").glob("*.duckdb")),
    ]:
        a = duckdb.connect(str(p), read_only=True)
        counts = {
            t: a.execute(f"SELECT count(*) FROM {t}").fetchone()[0]
            for (t,) in a.execute("SELECT table_name FROM duckdb_tables() ORDER BY 1").fetchall()
        }
        a.close()
        archives.append({**meta(p), "table_row_counts": counts, "sha256": sha256(p)})

    ext = [
        r[0]
        for r in con.execute(
            "SELECT extension_name FROM duckdb_extensions() WHERE loaded ORDER BY 1"
        ).fetchall()
    ]
    inventory = {
        "audit_time_utc": audit_time,
        "commit": git("rev-parse", "HEAD"),
        "branch": git("rev-parse", "--abbrev-ref", "HEAD"),
        "dirty_files": git("status", "--short").splitlines(),
        "python": sys.version,
        "platform": platform.platform(),
        "duckdb": duckdb.__version__,
        "duckdb_loaded_extensions": ext,
        "database": {
            "path": DB.relative_to(ROOT).as_posix(),
            "bytes": st.st_size,
            "mtime": datetime.fromtimestamp(st.st_mtime, UTC).isoformat(),
            "sha256": sha256(DB),
            "wal_present": (DB.parent / (DB.name + ".wal")).exists(),
            "opened_read_only": True,
        },
        "tables": {
            t: {
                "rows": con.execute(f"SELECT count(*) FROM {t}").fetchone()[0],
                "columns": [
                    {"name": c, "type": ty, "nullable": nl, "default": d}
                    for tt, c, ty, nl, d, _ in columns
                    if tt == t
                ],
                "primary_key": pk.get(t),
                "constraint_types": sorted({ty for tt, ty, _ in constraints if tt == t}),
            }
            for t in tables
        },
        "views": con.execute("SELECT count(*) FROM duckdb_views() WHERE NOT internal").fetchone()[
            0
        ],
        "sequences": con.execute("SELECT count(*) FROM duckdb_sequences()").fetchone()[0],
        "indexes_beyond_primary_keys": con.execute(
            "SELECT count(*) FROM duckdb_indexes()"
        ).fetchone()[0],
        "foreign_keys_declared": sum(1 for _, ty, _ in constraints if ty == "FOREIGN KEY"),
        "check_constraints_declared": sum(1 for _, ty, _ in constraints if ty == "CHECK"),
        "unique_constraints_declared": sum(1 for _, ty, _ in constraints if ty == "UNIQUE"),
        "schema_drift_vs_declared": drift,
        "declared_not_yet_applied": {
            t: {"columns": list(decl[t]), "reason": PENDING_APPLY[t]} for t in sorted(pending)
        },
        "files_under_data_cache": files,
        "archives_and_backups": archives,
        "ledger_reconciliation": reconcile,
    }
    (OUT / "inventory.json").write_text(
        json.dumps(inventory, indent=1, default=str), encoding="utf-8"
    )
    print(
        json.dumps(
            {
                k: inventory[k]
                for k in (
                    "audit_time_utc",
                    "commit",
                    "duckdb",
                    "database",
                    "schema_drift_vs_declared",
                    "ledger_reconciliation",
                )
            },
            indent=1,
            default=str,
        )
    )
    return 0 if reconcile["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
