# ruff: noqa: E501
"""R1 rehearsal step 1 (no migration): a consistent snapshot of the production file into a NEW
isolated destination, verified logically, plus the read-only collision preflight for the R1 relabel.
Method approved in TASK.md: in-memory coordinator, source ATTACH READ_ONLY, new destination ATTACH,
BEGIN, COPY FROM DATABASE s TO d, COMMIT. Refuses an existing destination; never terminates a writer,
never copies the raw file/WAL, never opens production writable. Source and destination handles stay
open for logical comparison, then all are closed before the destination is hashed. Run from repo root
with the project venv. Writes r1_snapshot_rehearsal.json beside this script."""

import hashlib
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

import duckdb

SRC = Path("data_cache/football.duckdb").resolve()
HERE = Path(__file__).parent


def sha256(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def main() -> int:
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    dest_dir = Path(f"artifacts/db-migration-rehearsal-{stamp}").resolve()
    dest = dest_dir / "football-rehearsal.duckdb"
    if dest_dir.exists() or dest.exists():
        print("REFUSED: destination exists", dest)
        return 2
    dest_dir.mkdir(parents=True)
    wal = Path(str(SRC) + ".wal")
    out: dict = {
        "stamp": stamp,
        "source": str(SRC),
        "destination": str(dest),
        "source_wal_present_before": wal.exists(),
        "source_sha256_before": sha256(SRC),
        "source_size_before": SRC.stat().st_size,
        "duckdb": duckdb.__version__,
    }
    con = duckdb.connect(":memory:")
    con.execute(f"ATTACH '{SRC.as_posix()}' AS s (READ_ONLY)")
    con.execute(f"ATTACH '{dest.as_posix()}' AS d")
    con.execute("BEGIN TRANSACTION")
    con.execute("COPY FROM DATABASE s TO d")
    con.execute("COMMIT")

    def cat(db: str):
        tabs = con.execute(
            "SELECT table_name, column_count, estimated_size FROM duckdb_tables() "
            f"WHERE database_name='{db}' ORDER BY 1"
        ).fetchall()
        cols = con.execute(
            "SELECT table_name, column_name, column_index, data_type, is_nullable FROM "
            f"duckdb_columns() WHERE database_name='{db}' ORDER BY 1, 3"
        ).fetchall()
        cons = con.execute(
            "SELECT table_name, constraint_type, constraint_column_names::VARCHAR FROM "
            f"duckdb_constraints() WHERE database_name='{db}' ORDER BY 1, 2, 3"
        ).fetchall()
        views = con.execute(
            f"SELECT view_name FROM duckdb_views() WHERE database_name='{db}' AND NOT internal"
        ).fetchall()
        idx = con.execute(
            f"SELECT index_name FROM duckdb_indexes() WHERE database_name='{db}'"
        ).fetchall()
        return (
            [t[:2] for t in tabs],
            cols,
            cons,
            sorted(views),
            sorted(idx),
        )

    cs, cd = cat("s"), cat("d")
    out["schema_identical"] = cs == cd
    out["tables"] = len(cs[0])
    out["columns"] = len(cs[1])
    per = {}
    ok = True
    for t, _ in cs[0]:
        n_s = con.execute(f'SELECT count(*) FROM s."{t}"').fetchone()[0]
        n_d = con.execute(f'SELECT count(*) FROM d."{t}"').fetchone()[0]
        # logical multiset equality: duplicates counted (EXCEPT ALL both directions) + order-free digest
        only_s = con.execute(
            f'SELECT count(*) FROM (SELECT * FROM s."{t}" EXCEPT ALL SELECT * FROM d."{t}")'
        ).fetchone()[0]
        only_d = con.execute(
            f'SELECT count(*) FROM (SELECT * FROM d."{t}" EXCEPT ALL SELECT * FROM s."{t}")'
        ).fetchone()[0]
        dig = lambda db, t=t: con.execute(  # noqa: E731
            f'SELECT coalesce(sum(hash(x)::HUGEINT), 0)::VARCHAR FROM {db}."{t}" x'
        ).fetchone()[0]
        ds, dd = dig("s"), dig("d")
        same = n_s == n_d and only_s == 0 and only_d == 0 and ds == dd
        ok &= same
        per[t] = {
            "rows_src": n_s,
            "rows_dest": n_d,
            "only_src": only_s,
            "only_dest": only_d,
            "digest_equal": ds == dd,
            "identical": same,
        }
    out["per_table"] = per
    out["all_tables_logically_identical"] = ok

    # read-only collision preflight for the R1 relabel (on the SOURCE through the same handle)
    q = lambda sql: con.execute(sql).fetchall()  # noqa: E731
    pre = {
        "legacy_fpl_rows": {
            t: q(f"SELECT count(*) FROM s.{t} WHERE source='fpl'")[0][0]
            for t in ("player_ids", "team_ids", "match_ids")
        },
        "target_labels_already_present": q(
            "SELECT 'player_ids', count(*) FROM s.player_ids WHERE source LIKE 'fpl-%' UNION ALL "
            "SELECT 'team_ids', count(*) FROM s.team_ids WHERE source LIKE 'fpl-%' UNION ALL "
            "SELECT 'match_ids', count(*) FROM s.match_ids WHERE source LIKE 'fpl-%'"
        ),
        # proof of season per legacy row, through dependent rows; need exactly one season each
        "player_ids_seasons_proven": q(
            "SELECT n_seasons, count(*) FROM (SELECT p.source_id, count(DISTINCT g.season) n_seasons "
            "FROM s.player_ids p LEFT JOIN (SELECT DISTINCT season, fpl_id FROM s.fpl_players_gw "
            "UNION SELECT DISTINCT season, fpl_id FROM s.fpl_points_gw) g "
            "ON g.fpl_id = TRY_CAST(p.source_id AS INTEGER) WHERE p.source='fpl' "
            "GROUP BY 1) GROUP BY 1 ORDER BY 1"
        ),
        "match_ids_seasons_proven": q(
            "SELECT n_seasons, count(*) FROM (SELECT m.source_id, count(DISTINCT x.season) n_seasons "
            "FROM s.match_ids m LEFT JOIN s.matches x ON x.match_id = m.match_id "
            "WHERE m.source='fpl' GROUP BY 1) GROUP BY 1 ORDER BY 1"
        ),
        "team_ids_seasons_proven": q(
            "SELECT n_seasons, count(*) FROM (SELECT t.source_id, count(DISTINCT x.season) n_seasons "
            "FROM s.team_ids t LEFT JOIN s.matches x ON t.team_id IN (x.home_team_id, x.away_team_id) "
            "WHERE t.source='fpl' GROUP BY 1) GROUP BY 1 ORDER BY 1"
        ),
        "distinct_seasons_in_db": q(
            "SELECT 'matches', season, count(*) FROM s.matches GROUP BY 2 UNION ALL "
            "SELECT 'fpl_players_gw', season, count(*) FROM s.fpl_players_gw GROUP BY 2 ORDER BY 1, 2"
        ),
        "fpl_ids_referenced_by_more_than_one_player": q(
            "SELECT count(*) FROM (SELECT source_id FROM s.player_ids WHERE source='fpl' "
            "GROUP BY 1 HAVING count(DISTINCT player_id) > 1)"
        )[0][0],
    }
    out["collision_preflight"] = pre
    con.close()
    out["destination_sha256"] = sha256(dest)
    out["destination_size"] = dest.stat().st_size
    out["source_sha256_after"] = sha256(SRC)
    out["source_unchanged"] = out["source_sha256_before"] == out["source_sha256_after"]
    out["source_wal_present_after"] = wal.exists()
    (HERE / "r1_snapshot_rehearsal.json").write_text(
        json.dumps(out, indent=1, default=str), encoding="utf-8"
    )
    print(json.dumps({k: v for k, v in out.items() if k != "per_table"}, indent=1, default=str))
    bad = [t for t, v in per.items() if not v["identical"]]
    print("tables not identical:", bad)
    return 0 if out["schema_identical"] and ok and out["source_unchanged"] else 1


if __name__ == "__main__":
    sys.exit(main())
