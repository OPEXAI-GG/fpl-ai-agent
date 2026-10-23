"""T06: export the refreshed working copy, verify the manifest, restore into a new file and
compare every table (count + order-free content hash). Never touches data_cache/."""
import json, shutil, sys
from pathlib import Path
from fpl_agent.db.database import FootballDB, verify_export
from fpl_agent.db.quality import table_digests

W = Path(sys.argv[1]).resolve()
src = W / "work-final-copy.duckdb"; shutil.copy(W / "work.duckdb", src)
exp, dst = W / "work-export", W / "work-restored.duckdb"
assert not exp.exists() and not dst.exists()
a = FootballDB(str(src)); counts = a.export_parquet(exp)
problems = verify_export(exp)
b = FootballDB(str(dst)); loaded = b.import_parquet(exp, require_manifest=True)
da, db_ = table_digests(a), table_digests(b)
skip = {"ingest_state"}  # restore writes one 'import' marker row; compared without it below
diff = sorted(t for t in da if t not in skip and da[t] != db_.get(t))
q = "SELECT count(*), sum(hash(t))::VARCHAR FROM ingest_state t WHERE source <> 'import'"
same_state = a.con.execute(q).fetchone() == b.con.execute(q).fetchone()
out = {"exported_tables": len(counts), "verify_export_problems": problems, "restored_rows": loaded,
       "tables_differing": diff, "ingest_state_equal_excluding_import_marker": same_state,
       "pass": not problems and not diff and same_state}
print(json.dumps(out, indent=1)); (W / "restore_check.json").write_text(json.dumps(out, indent=1))
