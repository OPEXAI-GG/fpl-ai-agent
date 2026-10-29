"""Exports carry a manifest and are swapped in only when complete; imports refuse a damaged export
and never duplicate rows; the restore/publish scripts fail closed instead of starting empty."""

import json
import os
import re
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path

import pandas as pd
import pytest

from fpl_agent.cli import main
from fpl_agent.db import ingest_football_data
from fpl_agent.db.database import ExportError, FootballDB, verify_export
from tests.helpers import FIX

LEDGERS = ("decisions", "projections", "reviews", "ingest_log", "lessons")


def seeded() -> FootballDB:
    db = FootballDB()
    ingest_football_data.load_csv(db, (FIX / "fd_E1_2026.csv").read_text(encoding="utf-8"), "E1",
                                  2026)  # fmt: skip
    at = datetime(2026, 9, 1, 10)
    db.append("decisions", pd.DataFrame([{"season": 2026, "gw": 3, "created_at": at,
                                          "mode": "dry", "payload": '{"xp": 1}'}]))  # fmt: skip
    db.append("projections", pd.DataFrame([{"run_id": "r", "model": "m", "created_at": at,
                                            "season": 2026, "gw": 3, "player_id": 1,
                                            "xpts": 2.5, "detail": "{}"}]))  # fmt: skip
    db.append("reviews", pd.DataFrame([{"season": 2026, "gw": 2, "created_at": at,
                                        "kind": "prediction", "payload": "{}"}]))  # fmt: skip
    return db


def test_roundtrip_with_manifest_and_every_table(tmp_path):
    db = seeded()
    counts = db.export_parquet(tmp_path / "out")
    manifest = json.loads((tmp_path / "out" / "manifest.json").read_text(encoding="utf-8"))
    assert set(manifest["tables"]) == set(counts)  # empty tables are exported too
    assert verify_export(tmp_path / "out") == []
    db2 = FootballDB()
    db2.import_parquet(tmp_path / "out", require_manifest=True)
    assert db2.counts() == {**db.counts(), "ingest_state": db2.counts()["ingest_state"]}


def test_an_emptied_table_stays_empty_after_reexport(tmp_path):
    db = seeded()
    db.export_parquet(tmp_path / "out")
    db.con.execute("DELETE FROM decisions")
    db.export_parquet(tmp_path / "out")  # same folder: the old decisions file must not survive
    fresh = FootballDB()
    fresh.import_parquet(tmp_path / "out")
    assert fresh.counts()["decisions"] == 0


def test_repeated_import_adds_no_ledger_rows(tmp_path):
    db = seeded()
    db.export_parquet(tmp_path / "a")
    target = FootballDB()
    target.import_parquet(tmp_path / "a")
    once = target.counts()
    assert target.import_parquet(tmp_path / "a") == 0  # the same export is recognised
    db.export_parquet(tmp_path / "b")  # a new export holding the same rows
    target.import_parquet(tmp_path / "b")
    again = target.counts()
    for t in LEDGERS:
        assert again[t] == once[t], t


def test_export_and_import_under_a_path_with_an_apostrophe(tmp_path):
    out = tmp_path / "o'brien's data" / "out"
    db = seeded()
    db.export_parquet(out)
    fresh = FootballDB()
    fresh.import_parquet(out)
    assert fresh.counts()["matches"] == db.counts()["matches"] > 0


@pytest.mark.parametrize("damage", ["tamper", "delete", "manifest"])
def test_a_damaged_export_is_refused_and_nothing_is_loaded(tmp_path, damage):
    db = seeded()
    out = tmp_path / "out"
    db.export_parquet(out)
    if damage == "tamper":
        with open(out / "matches.parquet", "ab") as f:
            f.write(b"x")
    elif damage == "delete":
        (out / "decisions.parquet").unlink()
    else:
        (out / "manifest.json").write_text("{not json", encoding="utf-8")
    assert verify_export(out)
    fresh = FootballDB()
    with pytest.raises(ExportError):
        fresh.import_parquet(out)
    assert sum(fresh.counts().values()) == 0
    assert main(["verify-export", str(out)]) == 1


def test_legacy_exports_without_a_manifest_load_only_when_allowed(tmp_path):
    db = seeded()
    db.export_parquet(tmp_path / "out")
    (tmp_path / "out" / "manifest.json").unlink()
    FootballDB().import_parquet(tmp_path / "out")  # the current release archive has none
    with pytest.raises(ExportError):
        FootballDB().import_parquet(tmp_path / "out", require_manifest=True)


def test_previous_exports_are_kept_for_rollback(tmp_path):
    db = seeded()
    for _ in range(4):
        db.export_parquet(tmp_path / "out", keep=2)
    prev = sorted(p.name for p in tmp_path.glob("out.prev-*"))
    assert len(prev) == 2 and verify_export(tmp_path / prev[-1]) == []
    assert not list(tmp_path.glob("out.staging-*"))
    assert main(["verify-export", str(tmp_path / "out")]) == 0


def _rewrite(out, name, sql):
    """Replace one table file with `sql` over it and re-sign the manifest, as a consistent but
    differently shaped export (a schema change, or a hand-made file) would look."""
    import duckdb

    f = out / f"{name}.parquet"
    tmp = out / f"{name}.new.parquet"
    con = duckdb.connect()
    src = sql.format(f=f"read_parquet('{f.as_posix()}')")
    con.execute(f"COPY ({src}) TO '{tmp.as_posix()}' (FORMAT parquet)")
    cols = [[c[0], c[1]] for c in con.execute(f"DESCRIBE FROM '{tmp.as_posix()}'").fetchall()]
    rows = con.execute(f"SELECT count(*) FROM '{tmp.as_posix()}'").fetchone()[0]
    con.close()
    tmp.replace(f)
    m = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
    from fpl_agent.db.database import _sha256

    m["tables"][name] = {"rows": rows, "columns": cols, "sha256": _sha256(f)}
    (out / "manifest.json").write_text(json.dumps(m), encoding="utf-8")


def test_reordered_columns_load_by_name(tmp_path):
    db = seeded()
    out = tmp_path / "out"
    db.export_parquet(out)
    _rewrite(out, "matches", "SELECT away_goals, home_goals, * EXCLUDE (away_goals, home_goals) "
                             "FROM {f}")  # fmt: skip
    fresh = FootballDB()
    fresh.import_parquet(out, require_manifest=True)
    q = "SELECT match_id, home_goals, away_goals FROM matches ORDER BY 1"
    assert fresh.con.execute(q).fetchall() == db.con.execute(q).fetchall()


def test_a_file_with_an_undeclared_column_is_refused(tmp_path):
    db = seeded()
    out = tmp_path / "out"
    db.export_parquet(out)
    _rewrite(out, "matches", "SELECT *, 1 AS surprise FROM {f}")
    assert any("surprise" in p for p in verify_export(out))
    fresh = FootballDB()
    with pytest.raises(ExportError):
        fresh.import_parquet(out)
    assert sum(fresh.counts().values()) == 0


def test_manifest_columns_must_match_the_file(tmp_path):
    db = seeded()
    out = tmp_path / "out"
    db.export_parquet(out)
    m = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
    m["tables"]["matches"]["columns"] = m["tables"]["matches"]["columns"][::-1]
    (out / "manifest.json").write_text(json.dumps(m), encoding="utf-8")
    assert any("columns" in p for p in verify_export(out))


@pytest.mark.parametrize("manifest", [
    "[]", '{"version": 1, "tables": []}', '{"version": 1, "tables": {"matches": 3}}',
    '{"version": 1, "tables": {"../escape": {"rows": 0, "sha256": "x"}}}',
    '{"version": 1, "tables": {"not_a_table": {"rows": 0, "sha256": "x"}}}',
])  # fmt: skip
def test_malformed_or_unknown_manifest_entries_are_refused(tmp_path, manifest):
    db = seeded()
    out = tmp_path / "out"
    db.export_parquet(out)
    (out / "manifest.json").write_text(manifest, encoding="utf-8")
    assert verify_export(out)
    fresh = FootballDB()
    with pytest.raises(ExportError):
        fresh.import_parquet(out)
    assert sum(fresh.counts().values()) == 0


@pytest.mark.parametrize("change", ["unlisted", "added", "tampered"])
def test_a_table_file_the_manifest_does_not_list_is_refused(tmp_path, change):
    """The manifest is authoritative: a table file it does not list (its entry removed, a file
    added, or an unlisted file altered) is unverified, so the whole import is refused."""
    db = seeded()
    out = tmp_path / "out"
    db.export_parquet(out)
    m = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
    if change == "added":
        (out / "decisions.parquet").rename(out / "stray.parquet")
        shutil.copy(out / "stray.parquet", out / "decisions.parquet")
    else:
        del m["tables"]["matches"]
    (out / "manifest.json").write_text(json.dumps(m), encoding="utf-8")
    if change == "tampered":
        with open(out / "matches.parquet", "ab") as f:
            f.write(b"x")
    problems = verify_export(out)
    name = "stray" if change == "added" else "matches"
    assert any(p.startswith(f"{name}.parquet: file not listed") for p in problems)
    fresh = FootballDB()
    with pytest.raises(ExportError):
        fresh.import_parquet(out, require_manifest=True)
    assert sum(fresh.counts().values()) == 0
    assert main(["verify-export", str(out)]) == 1


def test_only_the_tables_a_manifest_lists_are_loaded(tmp_path, monkeypatch):
    """Even if verification were bypassed, a manifested import never reads an unlisted file."""
    import fpl_agent.db.database as database

    db = seeded()
    out = tmp_path / "out"
    db.export_parquet(out)
    m = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
    del m["tables"]["matches"]
    (out / "manifest.json").write_text(json.dumps(m), encoding="utf-8")
    monkeypatch.setattr(database, "verify_export", lambda d: [])
    fresh = FootballDB()
    fresh.import_parquet(out, require_manifest=True)
    assert fresh.counts()["matches"] == 0 and fresh.counts()["decisions"] == 1


def test_identical_ledger_rows_survive_a_roundtrip(tmp_path):
    """Two equal rows in a table without a key are two events; an import keeps both (and a
    repeated import of the same rows still adds none)."""
    db = seeded()
    at = datetime(2026, 9, 1, 10)
    for _ in range(2):
        db.con.execute("INSERT INTO ingest_log VALUES ('x', ?, 't', 1, '')", [at])
    db.export_parquet(tmp_path / "a")
    fresh = FootballDB()
    fresh.import_parquet(tmp_path / "a")
    n = "SELECT count(*) FROM ingest_log WHERE source = 'x'"
    assert fresh.con.execute(n).fetchone()[0] == 2
    db.export_parquet(tmp_path / "b")
    fresh.import_parquet(tmp_path / "b")
    assert fresh.con.execute(n).fetchone()[0] == 2


def test_an_older_export_without_the_identity_tables_still_loads(tmp_path):
    """An export written before `player_codes`/`migration_log` existed is a merge source, not
    a corrupt one: the missing tables load as nothing and are named by `missing_tables`."""
    from fpl_agent.db.database import missing_tables

    db = seeded()
    out = tmp_path / "out"
    db.export_parquet(out)
    m = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
    for name in ("player_codes", "migration_log"):
        del m["tables"][name]
        (out / f"{name}.parquet").unlink()
    (out / "manifest.json").write_text(json.dumps(m), encoding="utf-8")
    assert verify_export(out) == []
    assert missing_tables(out) == ["migration_log", "player_codes"]
    fresh = FootballDB()
    fresh.import_parquet(out, require_manifest=True)
    assert fresh.counts()["matches"] == db.counts()["matches"]


def test_opening_names_the_tables_it_had_to_create(tmp_path):
    path = tmp_path / "old.duckdb"
    db = FootballDB(path)
    assert len(db.created_tables) == len(db.counts())  # a new file: all of them
    db.con.execute("DROP TABLE player_codes")
    db.close()
    again = FootballDB(path)
    assert again.created_tables == ["player_codes"]
    again.close()
    assert FootballDB(path).created_tables == []


def _every_table_filled(db: FootballDB, rows: int = 2) -> None:
    """Distinct synthetic rows in every declared table (journals and attempts included), every
    column set, NULLs too, so a restore that drops a table, column or value shows."""
    from fpl_agent.db.database import declared_types

    lit = {"BIGINT": "{i}", "INTEGER": "{i}", "DOUBLE": "{i}.25", "BOOLEAN": "{b}",
           "DATE": "DATE '2026-09-0{i}'", "TIMESTAMP": "TIMESTAMP '2026-09-0{i} 10:00:00.123456'",
           "VARCHAR": "'{t}-{i} it''s'", "JSON": "'{{\"k\": {i}, \"s\": \"x y\"}}'"}  # fmt: skip
    for table, cols in declared_types().items():
        for i in range(1, rows + 1):
            vals = [lit[ty].format(i=i, b=str(i % 2 == 0).lower(), t=table) for _, ty in cols]
            if i == rows:  # a NULL in every nullable column of the last row (keys stay set)
                key = db.con.execute(
                    "SELECT constraint_column_names FROM duckdb_constraints() WHERE "
                    "table_name = ? AND constraint_type = 'PRIMARY KEY'", [table]).fetchone()  # fmt: skip
                keep = set(key[0]) if key else set()
                vals = [v if c in keep else "NULL" for (c, _), v in zip(cols, vals, strict=True)]
            db.con.execute(f"INSERT INTO {table} VALUES ({', '.join(vals)})")


def _rows(db: FootballDB, table: str) -> list:
    return sorted(map(repr, db.con.execute(f"SELECT * FROM {table}").fetchall()))


def test_a_full_restore_reproduces_every_current_table_exactly(tmp_path):
    """Restore = import into an empty database. Every declared table, the operational ones
    (repair journal, source attempts, migration log, ingest markers) included, comes back with
    the same rows and values; the only addition is the import's own marker."""
    from fpl_agent.db.schema import TABLES

    src = FootballDB()
    _every_table_filled(src)
    src.export_parquet(tmp_path / "out")
    dst = FootballDB()
    dst.import_parquet(tmp_path / "out", require_manifest=True)
    for t in TABLES:
        got = _rows(dst, t)
        if t == "ingest_state":
            got = [r for r in got if not r.startswith("('import',")]
        assert got == _rows(src, t), t
    assert {"repair_log", "source_attempts", "migration_log"} <= set(TABLES)
    assert all(_rows(src, t) for t in TABLES)  # none was empty, so none passed vacuously


def test_an_import_interrupted_part_way_leaves_nothing(tmp_path, monkeypatch):
    from fpl_agent.db import database

    src = FootballDB()
    _every_table_filled(src)
    src.export_parquet(tmp_path / "out")
    dst = FootballDB()
    real = database._undeclared
    seen = []

    def power_cut(name, cols):
        seen.append(name)
        if len(seen) == 10:  # nine tables already inserted in this transaction
            raise OSError("disk went away")
        return real(name, cols)

    monkeypatch.setattr(database, "_undeclared", power_cut)
    with pytest.raises(OSError):
        dst.import_parquet(tmp_path / "out", require_manifest=True)
    assert all(n == 0 for n in dst.counts().values())  # no rows, no "imported" marker either
    monkeypatch.setattr(database, "_undeclared", real)
    assert dst.import_parquet(tmp_path / "out", require_manifest=True) > 0  # a retry works


def test_a_file_replaced_after_verification_cannot_be_imported(tmp_path, monkeypatch):
    """Verification and loading read one private copy: swapping a verified file in the export
    folder (here the moment verification returns) changes nothing that is loaded."""
    from fpl_agent.db import database

    good = seeded()
    good.export_parquet(tmp_path / "out")
    evil = FootballDB()
    evil.con.execute("INSERT INTO teams VALUES (999, 'Planted', 'planted')")
    evil.export_parquet(tmp_path / "evil")
    real = database.verify_export

    def then_swap(d):
        problems = real(d)
        shutil.copyfile(tmp_path / "evil" / "teams.parquet", tmp_path / "out" / "teams.parquet")
        return problems

    monkeypatch.setattr(database, "verify_export", then_swap)
    dst = FootballDB()
    dst.import_parquet(tmp_path / "out", require_manifest=True)
    assert dst.con.execute("SELECT count(*) FROM teams WHERE team_id = 999").fetchone()[0] == 0
    assert dst.counts()["teams"] == good.counts()["teams"]
    monkeypatch.setattr(database, "verify_export", real)
    with pytest.raises(ExportError, match="checksum"):  # the swapped folder itself is refused
        FootballDB().import_parquet(tmp_path / "out")


def test_schema_require_refuses_without_writing_and_init_names_what_it_creates(tmp_path):
    import duckdb

    from fpl_agent.db.database import SchemaError, schema_status

    path = tmp_path / "old.duckdb"
    db = FootballDB(path)
    db.con.execute("DROP TABLE source_attempts")
    db.close()
    st = schema_status(path)
    assert st["missing_tables"] == ["source_attempts"] and not st["current"]
    before = path.read_bytes()
    with pytest.raises(SchemaError, match="missing declared tables"):
        FootballDB(path, schema="require")
    assert path.read_bytes() == before  # refused before any write
    assert FootballDB(path).created_tables == ["source_attempts"]  # explicit, and named
    assert schema_status(path)["current"]
    # a changed existing table is never altered by an open, in either mode
    con = duckdb.connect(str(path))
    con.execute("ALTER TABLE lessons DROP COLUMN rule")
    con.close()
    for mode in ("init", "require"):
        with pytest.raises(SchemaError, match="columns differ"):
            FootballDB(path, schema=mode)
    assert "lessons" in schema_status(path)["column_differences"]
    with pytest.raises(ValueError):
        FootballDB(schema="migrate")


def test_db_schema_command_is_read_only(tmp_path, capsys):
    path = tmp_path / "old.duckdb"
    db = FootballDB(path)
    db.con.execute("DROP TABLE repair_log")
    db.close()
    before = path.read_bytes()
    assert main(["db-schema", "--db", str(path)]) == 3
    out = json.loads(capsys.readouterr().out)
    assert out["missing_tables"] == ["repair_log"] and out["current"] is False
    assert path.read_bytes() == before
    FootballDB(path).close()
    assert main(["db-schema", "--db", str(path)]) == 0
    assert main(["db-schema", "--db", str(tmp_path / "absent.duckdb")]) == 1
    assert not (tmp_path / "absent.duckdb").exists()


# ---- the shell scripts, against a stand-in `gh` (Linux CI; needs bash, tar and zstd) ----------
ROOT = Path(__file__).parent.parent
needs_shell = pytest.mark.skipif(
    os.name != "posix" or not shutil.which("bash") or not shutil.which("zstd"),
    reason="needs bash, tar and zstd (checked in Linux CI)",
)
FAKE_GH = r"""#!/usr/bin/env bash
# stand-in for the GitHub CLI: FAKE_MODE = missing | auth | ok | dropdated | badrolling |
# keeprolling ; archives live in $FAKE_STORE, a file's mtime is its updatedAt
echo "$*" >> "$FAKE_STORE/calls"
case "$1 $2" in
  "release view")
    if [ "$FAKE_MODE" = missing ]; then echo "release not found" >&2; exit 1; fi
    if [ "$FAKE_MODE" = auth ]; then echo "HTTP 401: Bad credentials" >&2; exit 1; fi
    for f in "$FAKE_STORE"/*.tar.zst; do
      [ -e "$f" ] || continue
      case "$*" in
        *size*) echo "$(basename "$f") $(stat -c %s "$f")" ;;
        *updatedAt*) echo "$(basename "$f") $(date -u -r "$f" +%Y-%m-%dT%H:%M:%SZ)" ;;
        *) basename "$f" ;;
      esac
    done ;;
  "release download") cp "$FAKE_STORE/$5" "$7/" ;;
  "release upload")
    # dropdated: the dated upload "succeeds" but never appears on the release
    if [ "$FAKE_MODE" = dropdated ] && [[ "$(basename "$4")" == football-db-* ]]; then exit 0; fi
    # badrolling: the rolling replacement is cut short (an interrupted --clobber)
    if [ "$FAKE_MODE" = badrolling ] && [ "$(basename "$4")" = football-db.tar.zst ]; then
      head -c 10 "$4" > "$FAKE_STORE/football-db.tar.zst"; exit 0
    fi
    # keeprolling: the rolling replacement fails and the previous asset stays
    if [ "$FAKE_MODE" = keeprolling ] && [ "$(basename "$4")" = football-db.tar.zst ]; then
      echo "HTTP 502: upload failed" >&2; exit 1
    fi
    cp "$4" "$FAKE_STORE/" ;;
  "release create") ;;
esac
"""


@pytest.fixture
def fake_gh(tmp_path):
    store = tmp_path / "store"
    store.mkdir()
    gh = tmp_path / "gh"
    gh.write_text(FAKE_GH, encoding="utf-8")
    gh.chmod(0o755)
    env = {**os.environ, "GH": str(gh), "FAKE_STORE": str(store), "FAKE_MODE": "ok",
           "VERIFY": f"{sys.executable} -m fpl_agent.cli verify-export"}  # fmt: skip
    return store, env


def sh(script, arg, env):
    return subprocess.run(["bash", str(ROOT / "scripts" / script), str(arg)], env=env,
                          capture_output=True, text=True)  # fmt: skip


@needs_shell
def test_publish_then_restore_roundtrip(tmp_path, fake_gh):
    store, env = fake_gh
    seeded().export_parquet(tmp_path / "out")
    assert sh("data_publish.sh", tmp_path / "out", env).returncode == 0
    names = sorted(p.name for p in store.glob("*.tar.zst"))
    assert names[0] == "football-db.tar.zst" and names[1].startswith("football-db-")
    res = sh("data_restore.sh", tmp_path / "restored", env)
    assert res.returncode == 0, res.stderr
    assert verify_export(tmp_path / "restored") == []
    dated = {**env, "DATA_ASSET": names[1]}  # roll back to a dated copy
    assert sh("data_restore.sh", tmp_path / "rollback", dated).returncode == 0


@needs_shell
def test_publish_refuses_an_absent_or_damaged_export(tmp_path, fake_gh):
    store, env = fake_gh
    assert sh("data_publish.sh", tmp_path / "nothing", env).returncode != 0
    seeded().export_parquet(tmp_path / "out")
    (tmp_path / "out" / "players.parquet").unlink()
    assert sh("data_publish.sh", tmp_path / "out", env).returncode != 0
    assert not list(store.glob("*.tar.zst"))


@needs_shell
def test_restore_starts_empty_only_when_there_is_no_release(tmp_path, fake_gh):
    store, env = fake_gh
    res = sh("data_restore.sh", tmp_path / "a", {**env, "FAKE_MODE": "missing"})
    assert res.returncode == 0 and "starting empty" in res.stdout
    assert sh("data_restore.sh", tmp_path / "b", {**env, "FAKE_MODE": "auth"}).returncode != 0


@needs_shell
def test_restore_fails_on_a_corrupt_archive(tmp_path, fake_gh):
    store, env = fake_gh
    (store / "football-db.tar.zst").write_bytes(b"not an archive")
    res = sh("data_restore.sh", tmp_path / "x", env)
    assert res.returncode != 0
    assert not (tmp_path / "x").exists() or not any((tmp_path / "x").iterdir())


def test_an_export_is_one_snapshot_while_another_connection_writes(tmp_path, monkeypatch):
    """A second connection to the same file (the dashboard's job runner, a notebook) that commits
    half way through an export must not leave the export with tables from two moments."""
    import duckdb

    from fpl_agent.db import database

    path = tmp_path / "f.duckdb"
    db = FootballDB(path)
    seeded_rows = seeded()
    db.import_parquet(_export(seeded_rows, tmp_path / "seed"))
    other = duckdb.connect(str(path))
    real = database._sha256
    wrote = {"done": False}

    def write_between(p):
        if not wrote["done"] and Path(p).name == "matches.parquet":
            other.execute("INSERT INTO decisions (season, gw, mode) VALUES (2026, 9, 'dry')")
            other.execute("DELETE FROM team_match")
            wrote["done"] = True
        return real(p)

    monkeypatch.setattr(database, "_sha256", write_between)
    before = db.counts()
    counts = db.export_parquet(tmp_path / "out")
    assert wrote["done"]
    assert (
        counts["decisions"] == before["decisions"] and counts["team_match"] == before["team_match"]
    )
    m = json.loads((tmp_path / "out" / "manifest.json").read_text(encoding="utf-8"))
    assert m["tables"]["team_match"]["rows"] == before["team_match"] > 0
    other.close()
    db.close()


def _export(db, out):
    db.export_parquet(out)
    return out


def test_a_failed_swap_keeps_the_last_good_export_in_place(tmp_path, monkeypatch):
    db = seeded()
    out = tmp_path / "out"
    db.export_parquet(out)
    good = (out / "manifest.json").read_bytes()
    real = Path.rename

    def failing(self, target):
        if ".staging-" in self.name:
            raise OSError("disk full")
        return real(self, target)

    monkeypatch.setattr(Path, "rename", failing)
    db.con.execute("DELETE FROM decisions")
    with pytest.raises(OSError):
        db.export_parquet(out)
    assert (out / "manifest.json").read_bytes() == good and verify_export(out) == []
    assert not list(tmp_path.glob("out.staging-*"))


def test_a_briefly_locked_folder_is_retried(tmp_path, monkeypatch):
    from fpl_agent.db import database

    db = seeded()
    out = tmp_path / "out"
    db.export_parquet(out)
    real = Path.rename
    denied = {"n": 0}

    def flaky(self, target):
        if ".staging-" in self.name and denied["n"] < 2:
            denied["n"] += 1
            raise PermissionError("[WinError 5] Access is denied")
        return real(self, target)

    monkeypatch.setattr(Path, "rename", flaky)
    monkeypatch.setattr(database.time, "sleep", lambda s: None)
    db.export_parquet(out)
    assert denied["n"] == 2 and verify_export(out) == []


def _drop_from_export(out: Path, table: str) -> None:
    """An internally consistent export that simply lacks `table` (an older export)."""
    m = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
    del m["tables"][table]
    (out / "manifest.json").write_text(json.dumps(m), encoding="utf-8")
    (out / f"{table}.parquet").unlink()


def test_verify_export_complete_requires_every_declared_table(tmp_path, capsys):
    seeded().export_parquet(tmp_path / "out")
    assert main(["verify-export", "--complete", str(tmp_path / "out")]) == 0
    _drop_from_export(tmp_path / "out", "transfers")
    assert main(["verify-export", str(tmp_path / "out")]) == 0  # still a valid older export
    assert main(["verify-export", "--complete", str(tmp_path / "out")]) == 1
    assert "transfers: declared table not in the export" in capsys.readouterr().out


def test_a_fresh_export_lists_every_declared_table_even_when_empty(tmp_path):
    from fpl_agent.db.database import missing_tables
    from fpl_agent.db.schema import TABLES

    FootballDB().export_parquet(tmp_path / "out")
    m = json.loads((tmp_path / "out" / "manifest.json").read_text(encoding="utf-8"))
    assert set(m["tables"]) == set(TABLES)
    assert all(t["rows"] == 0 for t in m["tables"].values())
    assert missing_tables(tmp_path / "out") == [] and verify_export(tmp_path / "out") == []


@needs_shell
def test_publish_refuses_an_export_lacking_a_declared_table(tmp_path, fake_gh):
    store, env = fake_gh
    seeded().export_parquet(tmp_path / "out")
    _drop_from_export(tmp_path / "out", "transfers")
    res = sh("data_publish.sh", tmp_path / "out", env)
    assert res.returncode != 0
    assert "transfers: declared table not in the export" in res.stdout + res.stderr
    assert not list(store.glob("*.tar.zst"))


@needs_shell
def test_publish_refuses_an_archive_over_the_size_bound(tmp_path, fake_gh):
    store, env = fake_gh
    seeded().export_parquet(tmp_path / "out")
    res = sh("data_publish.sh", tmp_path / "out", {**env, "MAX_ARCHIVE_BYTES": "10"})
    assert res.returncode != 0 and "MAX_ARCHIVE_BYTES" in res.stderr
    assert not list(store.glob("*.tar.zst"))


@needs_shell
def test_publish_keeps_the_rolling_asset_when_the_dated_copy_is_missing(tmp_path, fake_gh):
    store, env = fake_gh
    (store / "football-db.tar.zst").write_bytes(b"previous good asset")
    seeded().export_parquet(tmp_path / "out")
    res = sh("data_publish.sh", tmp_path / "out", {**env, "FAKE_MODE": "dropdated"})
    assert res.returncode != 0 and "left unchanged" in res.stderr
    assert (store / "football-db.tar.zst").read_bytes() == b"previous good asset"


@needs_shell
def test_restore_accepts_a_legacy_archive_without_a_manifest(tmp_path, fake_gh):
    store, env = fake_gh
    out = tmp_path / "out"
    seeded().export_parquet(out)
    (out / "manifest.json").unlink()
    subprocess.run(["tar", "--zstd", "-cf", str(store / "football-db.tar.zst"), "-C", str(out),
                    "."], check=True)  # fmt: skip
    res = sh("data_restore.sh", tmp_path / "restored", env)
    assert res.returncode == 0 and "legacy export" in res.stdout, res.stderr
    db = FootballDB()
    assert db.import_parquet(tmp_path / "restored") > 0


@needs_shell
@pytest.mark.parametrize("member", ["link", "nested"])
def test_restore_refuses_an_archive_with_unexpected_members(tmp_path, fake_gh, member):
    store, env = fake_gh
    out = tmp_path / "out"
    seeded().export_parquet(out)
    if member == "link":
        (out / "evil.parquet").symlink_to("/etc/passwd")
    else:
        (out / "sub").mkdir()
        (out / "sub" / "x.parquet").write_bytes(b"x")
    subprocess.run(["tar", "--zstd", "-cf", str(store / "football-db.tar.zst"), "-C", str(out),
                    "."], check=True)  # fmt: skip
    res = sh("data_restore.sh", tmp_path / "restored", env)
    assert res.returncode != 0 and "not allowed" in res.stderr
    assert not (tmp_path / "restored").exists()


@needs_shell
def test_restore_refuses_an_asset_name_that_is_not_an_export(tmp_path, fake_gh):
    store, env = fake_gh
    res = sh("data_restore.sh", tmp_path / "r", {**env, "DATA_ASSET": "*"})
    assert res.returncode != 0 and "DATA_ASSET must be" in res.stderr
    assert not (store / "calls").exists()  # refused before asking the release anything


@needs_shell
def test_publish_refuses_a_folder_restore_would_refuse(tmp_path, fake_gh):
    store, env = fake_gh
    seeded().export_parquet(tmp_path / "out")
    (tmp_path / "out" / "notes.txt").write_text("stray", encoding="utf-8")
    res = sh("data_publish.sh", tmp_path / "out", env)
    assert res.returncode != 0 and "not allowed" in res.stderr
    assert not list(store.glob("*.tar.zst"))


@needs_shell
def test_publish_checks_the_rolling_asset_and_restore_falls_back_to_the_dated_copy(
    tmp_path, fake_gh
):
    store, env = fake_gh
    seeded().export_parquet(tmp_path / "out")
    res = sh("data_publish.sh", tmp_path / "out", {**env, "FAKE_MODE": "badrolling"})
    assert res.returncode != 0 and "falls back to the newest dated copy" in res.stderr
    (store / "football-db.tar.zst").unlink()  # the clobber removed it and never finished
    res = sh("data_restore.sh", tmp_path / "restored", env)
    assert res.returncode == 0 and "falling back" in res.stderr, res.stderr
    assert verify_export(tmp_path / "restored") == []


@needs_shell
def test_restore_prefers_a_dated_copy_newer_than_a_stale_rolling_asset(tmp_path, fake_gh):
    store, env = fake_gh
    stale = store / "football-db.tar.zst"
    stale.write_bytes(b"previous run's asset")
    os.utime(stale, (946684800, 946684800))  # 2000-01-01: an earlier publish
    seeded().export_parquet(tmp_path / "out")
    res = sh("data_publish.sh", tmp_path / "out", {**env, "FAKE_MODE": "keeprolling"})
    assert res.returncode != 0
    assert stale.read_bytes() == b"previous run's asset"  # the replacement never happened
    res = sh("data_restore.sh", tmp_path / "restored", env)
    assert res.returncode == 0 and "is older than the newest dated copy" in res.stderr, res.stderr
    assert verify_export(tmp_path / "restored") == []  # this run's export, not the stale one


@needs_shell
def test_a_damaged_rolling_asset_fails_closed_and_names_the_dated_copy(tmp_path, fake_gh):
    store, env = fake_gh
    seeded().export_parquet(tmp_path / "out")
    assert sh("data_publish.sh", tmp_path / "out", {**env, "FAKE_MODE": "badrolling"}).returncode
    res = sh("data_restore.sh", tmp_path / "restored", env)
    assert res.returncode != 0
    assert not (tmp_path / "restored").exists()
    dated = re.search(r"DATA_ASSET=(football-db-\S+)", res.stderr)
    assert dated, res.stderr
    res = sh("data_restore.sh", tmp_path / "recovered", {**env, "DATA_ASSET": dated.group(1)})
    assert res.returncode == 0, res.stderr
    assert verify_export(tmp_path / "recovered") == []


def test_default_verify_never_syncs_packages():
    for script in ("data_publish.sh", "data_restore.sh"):
        text = (ROOT / "scripts" / script).read_text(encoding="utf-8")
        assert 'VERIFY="${VERIFY:-uv run --no-sync fpl-agent verify-export}"' in text
