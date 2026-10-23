"""Exports carry a manifest and are swapped in only when complete; imports refuse a damaged export
and never duplicate rows; the restore/publish scripts fail closed instead of starting empty."""

import json
import os
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


# ---- the shell scripts, against a stand-in `gh` (Linux CI; needs bash, tar and zstd) ----------
ROOT = Path(__file__).parent.parent
needs_shell = pytest.mark.skipif(
    os.name != "posix" or not shutil.which("bash") or not shutil.which("zstd"),
    reason="needs bash, tar and zstd (checked in Linux CI)",
)
FAKE_GH = r"""#!/usr/bin/env bash
# stand-in for the GitHub CLI: FAKE_MODE = missing | auth | ok ; archives live in $FAKE_STORE
echo "$*" >> "$FAKE_STORE/calls"
case "$1 $2" in
  "release view")
    if [ "$FAKE_MODE" = missing ]; then echo "release not found" >&2; exit 1; fi
    if [ "$FAKE_MODE" = auth ]; then echo "HTTP 401: Bad credentials" >&2; exit 1; fi
    ls "$FAKE_STORE" | grep '\.tar\.zst$' || true ;;
  "release download") cp "$FAKE_STORE/$5" "$7/" ;;
  "release upload") cp "$4" "$FAKE_STORE/" ;;
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
