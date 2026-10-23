# ruff: noqa: E501
"""DB01: configured stores and paths (defaults found in code) against what exists on disk, plus
presence-only (boolean) effective configuration of THIS shell. Secrets are never read or printed.
The dashboard or scheduler may run with a different environment than this shell. Writes
storage_paths.json. Run from the repo root."""

import json
import os
from pathlib import Path

ROOT = Path.cwd()
env_names = [
    "SNAPSHOT_DIR",
    "AGENT_MODE",
    "AGENT_ENABLED",
    "AGENT_MAX_SNAPSHOT_HOURS",
    "FPL_ENTRY_ID",
    "ODDS_API_KEY",
]
stores = [
    (
        "football database (cli --db and pipeline default)",
        "data_cache/football.duckdb",
        "src/fpl_agent/cli.py:467-581",
    ),
    (
        "SnapshotStore point-in-time JSON",
        "data_cache/snapshots",
        "src/fpl_agent/config.py:34,51; data/store.py",
    ),
    (
        "snapshot parquet frames",
        "data_cache/snapshots/frames",
        "data/store.py write_frame; cli.py:60",
    ),
    ("dashboard state dir", "data_cache/dashboard", "src/fpl_agent/cli.py:594"),
    ("dashboard jobs file", "data_cache/dashboard/jobs.json", "dashboard/jobs.py"),
    ("dashboard preferences", "data_cache/dashboard/preferences.json", "dashboard/service.py"),
    ("dashboard plans", "data_cache/dashboard/plans", "dashboard/service.py"),
    (
        "parquet export folder (db-update/--export, pipeline.py:409)",
        "(argument; none found under data_cache)",
        "db/database.py:390",
    ),
    ("pre-change backups (manual copies)", "data_cache/backups", "no code path found"),
    (
        "calibration archive",
        "data_cache/calibration-study-20261020.duckdb",
        "no code path found; historical seasons 2024-2025",
    ),
    (
        "GitHub release asset 'data' (parquet export + manifest), written by .github/workflows/data.yml and backfill.yml (history from 2017/18)",
        "(remote; NOT inspected: no network or credentials used)",
        ".github/workflows/data.yml, backfill.yml; scripts/data_publish.sh",
    ),
    (
        "data release scripts",
        "scripts/data_publish.sh, scripts/data_restore.sh, scripts/plan_publish.sh",
        "release assets; DB05",
    ),
]
out = {
    "stores": [
        {
            "store": s,
            "path": p,
            "defined_at": c,
            "exists_on_disk": (ROOT / p).exists()
            if not p.startswith("(") and "," not in p
            else None,
        }
        for s, p, c in stores
    ],
    "effective_environment_presence_this_shell": {n: bool(os.environ.get(n)) for n in env_names},
    ".env_file_present": (ROOT / ".env").exists(),
    "gitignored": "data_cache/ and *.duckdb are ignored (.gitignore)",
    "note": "defaults are not effective configuration; the dashboard server and any scheduler "
    "were not inspected, so SNAPSHOT_DIR, ODDS_API_KEY etc. may differ there",
}
Path(__file__).with_name("storage_paths.json").write_text(
    json.dumps(out, indent=1), encoding="utf-8"
)
print(json.dumps(out, indent=1))
