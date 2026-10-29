# ruff: noqa: E501
"""R1 migration rehearsal on DISPOSABLE COPIES of the verified snapshot (never production).

Baseline  : artifacts/db-migration-rehearsal-20261022T122715Z/football-rehearsal.duckdb (read only;
            its sha256 must equal the value recorded in r1_snapshot_rehearsal.json before and after).
Copies    : <new dir>/pre-migration.duckdb (untouched evidence), migrated.duckdb (migrate + idempotent
            re-run), ingest.duckdb (migrate, then current-season re-ingestion from stored raw rows),
            rollback.duckdb (migrate, then rollback; must equal pre-migration), interrupted.duckdb
            (migration forced to fail before COMMIT; must equal pre-migration).
Refuses an existing destination or any path under data_cache/. A failed run keeps its files and writes
FAILED.txt; exit status is non-zero on any failed assertion (including a bad collision preflight).
Run from the repo root with the project venv; writes r1_migration_rehearsal.json beside this script."""

import hashlib
import json
import shutil
import sys
from datetime import UTC, datetime
from pathlib import Path

import duckdb

from fpl_agent.dashboard.service import Reader
from fpl_agent.db import fpl_identity as fi
from fpl_agent.db import ingest_fpl
from fpl_agent.db.database import FootballDB
from fpl_agent.db.quality import run_checks
from fpl_agent.db.schema import TABLES
from fpl_agent.review import _cutoff

ROOT = Path(".").resolve()
HERE = Path(__file__).parent
BASELINE = ROOT / "artifacts/db-migration-rehearsal-20261022T122715Z/football-rehearsal.duckdb"
EXPECT = {
    "player_ids": 668,
    "team_ids": 20,
    "match_ids": 380,
}  # expectations to verify, not a licence
ID_TABLES = tuple(EXPECT)
FIXED_NOW = datetime(2026, 10, 22, 12, 0, 0)
# declared schema delta of this change (schema.TABLES): not in the production file yet
NEW_TABLES = (fi.JOURNAL, "player_codes")

results: dict = {"checks": []}


def sha256(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def check(name: str, ok: bool, detail=None) -> None:
    results["checks"].append({"check": name, "ok": bool(ok), "detail": detail})
    print(("PASS " if ok else "FAIL ") + name, "" if ok else detail)


def tables(con, schema="main") -> list[str]:
    return sorted(r[0] for r in con.execute("SELECT table_name FROM information_schema.tables "
                                            "WHERE table_schema = 'main' AND table_type = 'BASE TABLE'").fetchall())  # fmt: skip


def schema_of(con) -> list:
    cols = con.execute("SELECT table_name, ordinal_position, column_name, data_type, is_nullable, column_default "
                       "FROM information_schema.columns WHERE table_schema = 'main' ORDER BY 1, 2").fetchall()  # fmt: skip
    cons = con.execute("SELECT table_name, constraint_type, constraint_column_names::VARCHAR FROM duckdb_constraints() "
                       "ORDER BY 1, 2, 3").fetchall()  # fmt: skip
    return [cols, cons]


def compare(path_a: Path, path_b: Path, skip=(), allow_label_change=(), row_filters=None) -> dict:
    """Schema and every table's logical multiset (EXCEPT ALL both ways), a vs b. Tables in
    `allow_label_change` are compared ignoring the `source` column's value (relabelled ids)."""
    con = duckdb.connect(":memory:")
    con.execute(f"ATTACH '{path_a.as_posix()}' AS a (READ_ONLY)")
    con.execute(f"ATTACH '{path_b.as_posix()}' AS b (READ_ONLY)")
    out = {"schema_equal": None, "tables": {}}
    ca, cb = (
        duckdb.connect(str(path_a), read_only=True),
        duckdb.connect(str(path_b), read_only=True),
    )
    out["schema_equal"] = schema_of(ca) == [
        [r for r in schema_of(cb)[0] if r[0] not in skip],
        [r for r in schema_of(cb)[1] if r[0] not in skip],
    ]
    ca.close()
    cb.close()
    for t in tables(duckdb.connect(str(path_a), read_only=True)):
        if t in skip:
            continue
        flt = (row_filters or {}).get(t, "TRUE")
        n = con.execute(
            f"SELECT (SELECT count(*) FROM a.{t} WHERE {flt}), (SELECT count(*) FROM b.{t} WHERE {flt})"
        ).fetchone()
        sel = "* EXCLUDE (source)" if t in allow_label_change else "*"
        d1 = con.execute(
            f"SELECT count(*) FROM (SELECT {sel} FROM a.{t} WHERE {flt} EXCEPT ALL SELECT {sel} FROM b.{t} WHERE {flt})"
        ).fetchone()[0]
        d2 = con.execute(
            f"SELECT count(*) FROM (SELECT {sel} FROM b.{t} WHERE {flt} EXCEPT ALL SELECT {sel} FROM a.{t} WHERE {flt})"
        ).fetchone()[0]
        out["tables"][t] = {
            "rows_a": n[0],
            "rows_b": n[1],
            "only_a": d1,
            "only_b": d2,
            "equal": d1 == d2 == 0,
        }
    con.close()
    return out


def consumer_outputs(path: Path) -> dict:
    """What the dashboard, review and quality readers return for this file (fixed clock)."""
    ro = duckdb.connect(str(path), read_only=True)
    sections = Reader(ro, FIXED_NOW).build()
    ro.close()
    # declared difference only: the new journal table is not part of the comparison
    health = sections.get("health", {})
    health["tables"] = [t for t in health.get("tables", []) if t["table"] not in NEW_TABLES]
    ro2 = duckdb.connect(str(path), read_only=True)
    db = FootballDB.__new__(FootballDB)  # read-only shim: FootballDB() would create tables
    db.con = ro2
    season, gw = db.con.execute("SELECT season, max(gw) FROM fpl_players_gw GROUP BY season "
                                "ORDER BY season DESC LIMIT 1").fetchone()  # fmt: skip
    out = {
        "dashboard": json.dumps(sections, sort_keys=True, default=str),
        "review_cutoff": [str(_cutoff(db, season, g)) for g in range(1, gw + 2)],
        "quality": sorted((i.severity, i.check, i.detail) for i in run_checks(db)),
        "season_map_players": sorted(fi.season_map(db.con, "player", season).items()),
        "season_map_teams": sorted(fi.season_map(db.con, "team", season).items()),
        "season_map_matches": sorted(fi.season_map(db.con, "match", season).items()),
    }
    db.close()
    return out


def main() -> int:
    snap = json.loads((HERE / "r1_snapshot_rehearsal.json").read_text(encoding="utf-8"))
    base_before = sha256(BASELINE)
    results["baseline"] = {"path": str(BASELINE), "sha256_before": base_before,
                           "recorded_sha256": snap["destination_sha256"] if "destination_sha256" in snap else None}  # fmt: skip
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    out_dir = ROOT / f"artifacts/db-migration-rehearsal-{stamp}-r1"
    if out_dir.exists():
        print("REFUSED: destination exists", out_dir)
        return 2
    if "data_cache" in out_dir.parts:
        print("REFUSED: destination under data_cache")
        return 2
    out_dir.mkdir(parents=True)
    results.update(stamp=stamp, out_dir=str(out_dir), duckdb=duckdb.__version__)
    pre = out_dir / "pre-migration.duckdb"
    shutil.copy2(BASELINE, pre)
    results["pre_migration_sha256"] = sha256(pre)
    check(
        "baseline equals the hash recorded by the snapshot step",
        base_before == results["baseline"]["recorded_sha256"],
    )
    check(
        "pre-migration copy equals baseline bytes", results["pre_migration_sha256"] == base_before
    )

    # 1. read-only preflight on the pre-migration copy
    ro = duckdb.connect(str(pre), read_only=True)
    plan = fi.preflight(ro)
    ro.close()
    results["preflight"] = {"counts": plan.counts, "problems": plan.problems[:20], "other_sources": plan.other_sources,
                            "seasons": {k: sorted({v[2] for v in rows.values()}) for k, rows in plan.rows.items()}}  # fmt: skip
    check("preflight: no problems", not plan.problems, plan.problems[:5])
    check("preflight: counts match the audit expectations", plan.counts == EXPECT, plan.counts)
    before = consumer_outputs(pre)

    # 2. migrate a copy; idempotent re-run
    mig = out_dir / "migrated.duckdb"
    shutil.copy2(pre, mig)
    con = duckdb.connect(str(mig))
    try:
        r1 = fi.migrate(con)
        results["migrate"] = r1
        check(
            "migrate: migrated expected rows",
            r1["status"] == "migrated" and r1["counts"] == EXPECT,
            r1,
        )
        con.execute("CHECKPOINT")
        r2 = fi.migrate(con)
        check(
            "second run is a verified no-op",
            r2 == {"status": "noop-verified", "rows": sum(EXPECT.values())},
            r2,
        )
        journal = con.execute(
            f"SELECT table_name, count(*), min(old_source), min(new_source), count(DISTINCT new_source) FROM {fi.JOURNAL} GROUP BY 1 ORDER BY 1"
        ).fetchall()
        results["journal_summary"] = journal
        left = {
            t: con.execute(f"SELECT count(*) FROM {t} WHERE source = 'fpl'").fetchone()[0]
            for t in ID_TABLES
        }
        check("no legacy fpl identity rows remain", not any(left.values()), left)
    finally:
        con.close()
    cmp_ = compare(pre, mig, skip=NEW_TABLES, allow_label_change=ID_TABLES)
    results["pre_vs_migrated"] = cmp_
    bad = [t for t, v in cmp_["tables"].items() if not v["equal"]]
    check(
        "migrated: schema equal and every table equal (id tables ignoring source)",
        cmp_["schema_equal"] and not bad,
        bad,
    )
    # the id tables differ ONLY by the source label
    c2 = duckdb.connect(":memory:")
    c2.execute(f"ATTACH '{pre.as_posix()}' AS a (READ_ONLY)")
    c2.execute(f"ATTACH '{mig.as_posix()}' AS b (READ_ONLY)")
    labels = {}
    for t in ID_TABLES:
        labels[t] = {
            "before": c2.execute(
                f"SELECT source, count(*) FROM a.{t} GROUP BY 1 ORDER BY 1"
            ).fetchall(),
            "after": c2.execute(
                f"SELECT source, count(*) FROM b.{t} GROUP BY 1 ORDER BY 1"
            ).fetchall(),
        }
    c2.close()
    results["label_counts"] = labels
    check(
        "only declared label change: fpl -> fpl-2026",
        all(
            [x for x in v["before"] if x[0].startswith("fpl")] == [("fpl", EXPECT[t])]
            and [x for x in v["after"] if x[0].startswith("fpl")] == [("fpl-2026", EXPECT[t])]
            and [x for x in v["before"] if not x[0].startswith("fpl")]
            == [x for x in v["after"] if not x[0].startswith("fpl")]
            for t, v in labels.items()
        ),
        labels,
    )
    after = consumer_outputs(mig)
    for k in before:
        check(f"consumer output unchanged by migration: {k}", before[k] == after[k])

    # declared schema delta: the journal. Verify its schema and its complete content explicitly.
    jc = duckdb.connect(":memory:")
    jc.execute(f"ATTACH '{pre.as_posix()}' AS a (READ_ONLY)")
    jc.execute(f"ATTACH '{mig.as_posix()}' AS b (READ_ONLY)")
    declared = [c.split()[0] for c in TABLES[fi.JOURNAL].replace("PRIMARY KEY", ",").split(",")[:9]]
    live_cols = [
        r[0]
        for r in jc.execute(
            f"SELECT column_name FROM duckdb_columns() WHERE database_name = 'b' AND table_name = '{fi.JOURNAL}' ORDER BY column_index"
        ).fetchall()
    ]
    check(
        "journal columns equal the declared schema.TABLES entry", live_cols == declared, live_cols
    )
    bad_rows = 0
    for t, canon, named in (
        ("player_ids", "player_id", True),
        ("team_ids", "team_id", True),
        ("match_ids", "match_id", False),
    ):
        name_ok = (
            "AND j.source_name IS NOT DISTINCT FROM o.source_name AND j.source_name IS NOT DISTINCT FROM n.source_name"
            if named
            else ""
        )
        good = jc.execute(
            f"SELECT count(*) FROM b.{fi.JOURNAL} j JOIN a.{t} o ON o.source = j.old_source AND o.source_id = j.source_id "
            f"JOIN b.{t} n ON n.source = j.new_source AND n.source_id = j.source_id "
            f"WHERE j.table_name = '{t}' AND j.canonical_id = o.{canon} AND j.canonical_id = n.{canon} {name_ok} "
            "AND j.rolled_back_at IS NULL AND j.old_source = 'fpl' AND j.new_source = 'fpl-2026'"
        ).fetchone()[0]
        total = jc.execute(
            f"SELECT count(*) FROM b.{fi.JOURNAL} WHERE table_name = '{t}'"
        ).fetchone()[0]
        bad_rows += (total - good) + abs(total - EXPECT[t])
    check(
        "journal: every row matches its legacy and migrated identity row field by field",
        bad_rows == 0,
        bad_rows,
    )
    jc.close()

    # 3. current-season ingestion after migration, rebuilt from the copy's own stored raw rows
    ing = out_dir / "ingest.duckdb"
    shutil.copy2(mig, ing)
    db = FootballDB(ing)
    season, gw = db.con.execute(
        "SELECT season, max(gw) FROM fpl_players_gw GROUP BY season ORDER BY season DESC LIMIT 1"
    ).fetchone()
    elements = [
        json.loads(r[0])
        for r in db.con.execute(
            "SELECT raw FROM fpl_players_gw WHERE season = ? AND gw = ? QUALIFY row_number() OVER (PARTITION BY fpl_id ORDER BY snapshot_at DESC) = 1",
            [season, gw],
        ).fetchall()
    ]
    teams = [
        {"id": int(i), "name": n}
        for i, n in db.con.execute(
            "SELECT source_id, source_name FROM team_ids WHERE source = 'fpl-2026'"
        ).fetchall()
    ]
    raw_team = {
        int(i): t
        for i, t in db.con.execute(
            "SELECT source_id, team_id FROM team_ids WHERE source = 'fpl-2026'"
        ).fetchall()
    }
    fixtures = []
    for fid, ko, h, a, hg, ag, fin, md in db.con.execute(
        "SELECT i.source_id, m.kickoff, m.home_team_id, m.away_team_id, m.home_goals, m.away_goals, m.finished, m.matchday FROM match_ids i JOIN matches m USING (match_id) WHERE i.source = 'fpl-2026'"
    ).fetchall():
        inv = {t: r for r, t in raw_team.items()}
        fixtures.append(
            {
                "id": int(fid),
                "kickoff_time": ko.isoformat() + "Z",
                "team_h": inv[h],
                "team_a": inv[a],
                "event": md,
                "team_h_score": hg,
                "team_a_score": ag,
                "finished": fin,
            }
        )
    ids_before = {
        t: db.con.execute(f"SELECT * FROM {t} ORDER BY ALL").fetchall() for t in ID_TABLES
    }
    mt_before = db.con.execute("SELECT * FROM matches ORDER BY ALL").fetchall()
    ingest_fpl.load_bootstrap(
        db, {"teams": teams, "elements": elements}, season, gw, datetime(2026, 10, 22, 13, 0, 0)
    )
    ingest_fpl.load_fixtures(db, fixtures, {"teams": teams}, season)
    ids_after = {t: db.con.execute(f"SELECT * FROM {t} ORDER BY ALL").fetchall() for t in ID_TABLES}
    mt_after = db.con.execute("SELECT * FROM matches ORDER BY ALL").fetchall()
    results["ingestion_after_migration"] = {
        "season": season,
        "gw": gw,
        "elements": len(elements),
        "teams": len(teams),
        "fixtures": len(fixtures),
    }
    check(
        "re-ingesting the current season adds/changes no identity rows",
        ids_before == ids_after,
        {t: (len(ids_before[t]), len(ids_after[t])) for t in ID_TABLES},
    )
    check(
        "re-ingesting the current season leaves matches unchanged (scores, provenance)",
        mt_before == mt_after,
    )
    flags = db.con.execute(
        "SELECT count(*) FROM ingest_state WHERE source = 'link' AND key LIKE 'fpl-%'"
    ).fetchone()[0]
    rec = db.con.execute(
        "SELECT count(*), count(DISTINCT code) FROM player_codes WHERE source = 'fpl'"
    ).fetchone()
    mism = db.con.execute(
        "SELECT count(*) FROM player_codes c JOIN fpl_players_gw g ON json_extract_string(g.raw, '$.code') = c.code "
        "WHERE g.player_id <> c.player_id"
    ).fetchone()[0]
    results["codes_after_ingestion"] = {"recorded": rec, "flags": flags, "mismatches": mism}
    check(
        "re-ingestion recorded one code per player, none flagged or contradicting",
        rec == (668, 668) and flags == 0 and mism == 0,
        results["codes_after_ingestion"],
    )
    db.close()

    # 4. rollback returns the pre-migration state
    rb = out_dir / "rollback.duckdb"
    shutil.copy2(pre, rb)
    con = duckdb.connect(str(rb))
    fi.migrate(con)
    r = fi.rollback(con)
    con.execute("CHECKPOINT")
    con.close()
    check(
        "rollback restored the expected rows",
        r["status"] == "rolled-back" and r["rows"] == sum(EXPECT.values()),
        r,
    )
    cmp_rb = compare(pre, rb, skip=NEW_TABLES)
    results["pre_vs_rolled_back"] = cmp_rb
    bad = [t for t, v in cmp_rb["tables"].items() if not v["equal"]]
    check(
        "rolled back: schema and every table identical to pre-migration",
        cmp_rb["schema_equal"] and not bad,
        bad,
    )
    check("rolled back: consumer outputs identical", consumer_outputs(rb) == before)

    # 5. an interrupted migration leaves nothing behind
    it = out_dir / "interrupted.duckdb"
    shutil.copy2(pre, it)
    con = duckdb.connect(str(it))
    orig = fi._verify
    fi._verify = lambda *_a: (_ for _ in ()).throw(
        RuntimeError("simulated interruption before COMMIT")
    )
    try:
        fi.migrate(con)
        interrupted = False
    except RuntimeError:
        interrupted = True
    finally:
        fi._verify = orig
    con.execute("CHECKPOINT")
    con.close()
    check("interrupted migration raised", interrupted)
    cmp_it = compare(pre, it)
    bad = [t for t, v in cmp_it["tables"].items() if not v["equal"]]
    check(
        "interrupted migration: schema and every table identical, no journal",
        cmp_it["schema_equal"] and not bad and fi.JOURNAL not in cmp_it["tables"],
        bad,
    )

    # 6. export -> import keeps the journal; restored copy: no-op, then rollback to pre-migration
    src = out_dir / "export-source.duckdb"
    shutil.copy2(mig, src)
    xdb = FootballDB(src)
    seeded = fi.FplIdentity(xdb, 2026).seed_codes()
    counts = xdb.export_parquet(out_dir / "export")
    restored_path = out_dir / "restored.duckdb"
    rdb = FootballDB(restored_path)
    loaded = rdb.import_parquet(out_dir / "export", require_manifest=True)
    again = rdb.import_parquet(out_dir / "export", require_manifest=True)
    results["export_restore"] = {
        "seeded": seeded,
        "exported_rows": {k: counts[k] for k in (fi.JOURNAL, "player_codes")},
        "tables_loaded": loaded,
        "second_import": again,
    }
    check(
        "export carries the journal and codes",
        counts[fi.JOURNAL] == sum(EXPECT.values()) and counts["player_codes"] == 668,
        results["export_restore"],
    )
    check("restore loaded tables; a duplicate restore adds nothing", loaded > 0 and again == 0)
    noop = fi.migrate(rdb.con)
    check(
        "restored copy: migration is a verified no-op",
        noop == {"status": "noop-verified", "rows": sum(EXPECT.values())},
        noop,
    )
    back = fi.rollback(rdb.con)
    rdb.con.execute("CHECKPOINT")
    rdb.close()
    xdb.close()
    check(
        "restored copy: rollback restores the legacy rows",
        back["status"] == "rolled-back" and back["rows"] == sum(EXPECT.values()),
        back,
    )
    cmp_rs = compare(
        pre, restored_path, skip=NEW_TABLES, row_filters={"ingest_state": "source <> 'import'"}
    )
    results["pre_vs_restored_rolled_back"] = cmp_rs
    bad = [t for t, v in cmp_rs["tables"].items() if not v["equal"]]
    check(
        "restored + rolled back: every table identical to pre-migration",
        cmp_rs["schema_equal"] and not bad,
        bad,
    )

    base_after = sha256(BASELINE)
    results["baseline"]["sha256_after"] = base_after
    check("baseline snapshot unchanged", base_after == base_before)
    results["files"] = {
        p.name: {"bytes": p.stat().st_size, "sha256": sha256(p)}
        for p in sorted(out_dir.glob("*.duckdb"))
    }
    failed = [c["check"] for c in results["checks"] if not c["ok"]]
    results["failed"] = failed
    (HERE / "r1_migration_rehearsal.json").write_text(
        json.dumps(results, indent=1, default=str), encoding="utf-8"
    )
    if failed:
        (out_dir / "FAILED.txt").write_text("\n".join(failed), encoding="utf-8")
        print("REHEARSAL FAILED:", failed)
        return 1
    print("REHEARSAL COMPLETE:", len(results["checks"]), "checks passed; files in", out_dir)
    return 0


if __name__ == "__main__":
    sys.exit(main())
