from pathlib import Path
import hashlib
import json
import subprocess
import sys
from datetime import datetime, timezone

import duckdb
import numpy
import pandas

from fpl_agent.db.database import FootballDB, schema_status
from fpl_agent.db.quality import run_checks
from fpl_agent.db.source_health import report as source_report
from fpl_agent.db.eligibility import manifest
from fpl_agent.db.link_audit import coverage
from fpl_agent.model.features import player_gw_frame, match_frame
from fpl_agent.rules import Rules


root = Path(__file__).resolve().parent
dbpath = root / "work-data.duckdb"


def sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def git(*args: str) -> str:
    return subprocess.run(["git", *args], capture_output=True, text=True, check=True).stdout.strip()


source_files = sorted(
    [*Path("src/fpl_agent").rglob("*.py"), Path("rules/2026_27.yaml"),
     Path("pyproject.toml"), Path("TASK.md")]
    + ([Path("uv.lock")] if Path("uv.lock").exists() else [])
)
identity = {
    "captured_utc": datetime.now(timezone.utc).isoformat(),
    "git_head": git("rev-parse", "HEAD"),
    "branch": git("branch", "--show-current"),
    "working_tree_status": git("status", "--short"),
    "tracked_diff_sha256": hashlib.sha256(
        subprocess.run(["git", "diff", "HEAD", "--binary"], capture_output=True,
                       check=True).stdout
    ).hexdigest(),
    "source_file_hashes": {str(path): sha(path) for path in source_files},
    "duckdb": duckdb.__version__,
    "python": sys.version,
    "pandas": pandas.__version__,
    "numpy": numpy.__version__,
    "rules_sha256": sha(Path("rules/2026_27.yaml")),
    "experiment_spec_sha256": sha(Path("TASK.md")),
}
(root / "source-identity.json").write_text(json.dumps(identity, indent=2), encoding="utf-8")

pre = schema_status(dbpath)
with duckdb.connect(str(dbpath), read_only=True) as con:
    pre["table_names"] = [row[0] for row in con.execute(
        "SELECT table_name FROM information_schema.tables "
        "WHERE table_schema='main' ORDER BY 1"
    ).fetchall()]
(root / "schema-before-init.json").write_text(json.dumps(pre, indent=2, default=str), encoding="utf-8")

db = FootballDB(dbpath)
try:
    created_tables = list(db.created_tables)
    issues = run_checks(db, Rules.load())
    health = source_report(db, 2026)
    eligibility = manifest(db, 2026)
    link_coverage = coverage(db, 2026, limit=0)
    frame = player_gw_frame(db, min_season=2026)
    matches = match_frame(db)
    feature_coverage = {
        "season": 2026,
        "fpl_rows": len(frame),
        "fpl_players": int(frame.player_id.nunique()) if len(frame) else 0,
        "fpl_xg_nonnull": int(frame.xg.notna().sum()) if len(frame) else 0,
        "fpl_xa_nonnull": int(frame.xa.notna().sum()) if len(frame) else 0,
        "fpl_xg_nonnull_share": float(frame.xg.notna().mean()) if len(frame) else None,
        "fpl_xa_nonnull_share": float(frame.xa.notna().mean()) if len(frame) else None,
        "fotmob_match_frame_rows": len(matches),
        "fotmob_players": int(matches.player_id.nunique()) if len(matches) else 0,
        "fotmob_xg_nonnull": int(matches.xg.notna().sum()) if len(matches) else 0,
        "fotmob_xa_nonnull": int(matches.xa.notna().sum()) if len(matches) else 0,
    }
    result = {
        "schema_after": None,
        "created_tables_during_reconcile": created_tables,
        "quality_issues": [
            {"severity": issue.severity, "check": issue.check, "detail": issue.detail}
            for issue in issues
        ],
        "quality_issue_counts": {
            severity: sum(issue.severity == severity for issue in issues)
            for severity in ("error", "warn")
        },
        "source_health": health,
        "eligibility": {
            "season": eligibility["season"],
            "eligible_gws": eligibility["eligible_gws"],
            "reconstructed_gws": eligibility["reconstructed_gws"],
            "unavailable_gws": eligibility["unavailable_gws"],
            "excluded_matches": eligibility["excluded_matches"],
            "conflict_count": len(eligibility["conflicts"]),
        },
        "link_coverage": link_coverage,
        "feature_coverage": feature_coverage,
    }
    (root / "m00-data-quality-eligibility.json").write_text(
        json.dumps(result, indent=2, default=str), encoding="utf-8"
    )
finally:
    db.close()

after = schema_status(dbpath)
result["schema_after"] = after
(root / "m00-data-quality-eligibility.json").write_text(
    json.dumps(result, indent=2, default=str), encoding="utf-8"
)

print(json.dumps({"schema_before_init": pre, "data_sha256_after_init": sha(dbpath)},
                 indent=2, default=str))
