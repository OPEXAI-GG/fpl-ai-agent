"""G3 step B: legacy import, 23-table export + manifest, verify. Patched 51f0def+r3 code only."""
import hashlib, json, sys
from pathlib import Path
G3 = Path(sys.argv[1]); R = Path(sys.argv[2])
sys.path.insert(0, str(G3 / "src51" / "src"))
from fpl_agent.db import database
from fpl_agent.db.database import FootballDB, verify_export, ExportError
from fpl_agent.db.schema import TABLES
out = {"declared_tables": len(TABLES), "patched_module": database.__file__}
h = lambda p: hashlib.sha256(p.read_bytes()).hexdigest()
legacy = G3 / "w" / "legacy"
ref = json.loads((R / "artifacts/FAA-7-production/r20261028/parquet-hashes-before.json").read_text("utf-8-sig"))
ref = {Path(k).name: v for k, v in (ref.items() if isinstance(ref, dict) else ref)}
out["restored_equals_FAA7_hashes"] = all(h(legacy / n) == v for n, v in ref.items()) and len(ref) == len(list(legacy.glob("*.parquet")))
try:
    FootballDB(":memory:").import_parquet(legacy, require_manifest=True); out["require_manifest_refuses_legacy"] = False
except Exception as e:
    out["require_manifest_refuses_legacy"] = f"{type(e).__name__}: {e}"[:200]
dbp = G3 / "w" / "g3.duckdb"; assert not dbp.exists()
db = FootballDB(dbp)
out["imported_rows"] = db.import_parquet(legacy)
counts = db.counts()
exp = G3 / "w" / "export1"
out["export_counts"] = db.export_parquet(exp)
files = sorted(p.name for p in exp.iterdir())
out["export_files"] = len(files); out["parquet_files"] = sum(f.endswith(".parquet") for f in files)
out["has_manifest"] = (exp / "manifest.json").exists()
m = json.loads((exp / "manifest.json").read_text("utf-8"))
out["manifest_keys"] = sorted(m)[:10]
out["verify_export"] = verify_export(exp)
out["missing_tables"] = database.missing_tables(exp) if hasattr(database, "missing_tables") else "n/a"
ref_rows = json.loads((R / "artifacts/FAA-7-production/r20261028/materialize-rows.json").read_text("utf-8"))
out["row_mismatch_vs_FAA7"] = {k: (v, out["export_counts"].get(k)) for k, v in ref_rows.items()
    if k in TABLES and (v or 0) != out["export_counts"].get(k)}
out["empty_tables_exported"] = sorted(k for k, v in out["export_counts"].items() if v == 0)
db.close() if hasattr(db, "close") else None
(G3 / "ev" / "stepB.json").write_text(json.dumps(out, indent=1, default=str), "utf-8")
print(json.dumps({k: v for k, v in out.items() if k != "export_counts"}, indent=1, default=str))
