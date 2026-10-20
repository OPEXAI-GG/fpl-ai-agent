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
