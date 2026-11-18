"""`fpl-agent web local`: the data refresh from the release, when a new revision is published, and
revision pruning. Uses a synthetic database packed like the real `data` release and a file://
download, so nothing reaches the network."""

from __future__ import annotations

import io
import os
import tarfile
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
import zstandard

from fpl_agent.db.database import FootballDB
from fpl_agent.web import local, snapshots
from fpl_agent.web.config import WebSettings
from tests.web_support import SEASON, FakeFPL, make_service, make_world


@pytest.fixture(scope="module")
def world(tmp_path_factory):
    root = tmp_path_factory.mktemp("weblocal")
    db_path, boot, fx = make_world(root)
    return root, db_path, boot, fx


def pack(db_path: Path, out: Path) -> Path:
    """A .tar.zst of parquet tables, as scripts/data_publish.sh makes it."""
    export = out.parent / (out.stem + "-export")
    db = FootballDB.read_only(db_path)
    try:
        db.export_parquet(export)
    finally:
        db.close()
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as tar:
        for p in sorted(export.glob("*.parquet")):
            tar.add(p, arcname=p.name)
    out.write_bytes(zstandard.ZstdCompressor().compress(buf.getvalue()))
    return out


def asset_for(path: Path, updated: str = "2026-11-17T19:19:45Z", size: int | None = None):
    return local.Asset(path.resolve().as_uri(), size or path.stat().st_size, updated)


def settings(root: Path) -> WebSettings:
    return WebSettings(data_dir=root / "web", source_db=root / "work.duckdb", season=SEASON)


def test_refresh_downloads_imports_and_then_skips_until_the_release_changes(
    world, tmp_path, monkeypatch
):
    _, db_path, _, _ = world
    archive = pack(db_path, tmp_path / "football-db.tar.zst")
    s = settings(tmp_path)
    asset = asset_for(archive)
    monkeypatch.setattr(local, "release_asset", lambda *a, **k: asset)

    first = local.refresh_data(s)
    assert first["changed"] and s.source_db.exists()
    db = FootballDB.read_only(s.source_db)
    try:
        assert snapshots.describe(db, SEASON)["fpl_snapshot_at"]
    finally:
        db.close()
    assert not (s.source_db.parent / "local_download").exists()  # the download is cleaned up

    assert local.refresh_data(s)["changed"] is False  # same asset: nothing to do
    assert local.refresh_data(s, force=True)["changed"] is True

    newer = asset_for(archive, updated="2026-11-18T06:40:00Z")
    monkeypatch.setattr(local, "release_asset", lambda *a, **k: newer)
    assert local.refresh_data(s)["changed"] is True


def test_a_broken_download_keeps_the_database_you_have(world, tmp_path, monkeypatch):
    _, db_path, _, _ = world
    archive = pack(db_path, tmp_path / "football-db.tar.zst")
    s = settings(tmp_path)
    monkeypatch.setattr(local, "release_asset", lambda *a, **k: asset_for(archive))
    local.refresh_data(s)
    before = s.source_db.stat().st_mtime_ns

    short = asset_for(archive, updated="2026-11-19T00:00:00Z", size=archive.stat().st_size + 10)
    monkeypatch.setattr(local, "release_asset", lambda *a, **k: short)
    with pytest.raises(OSError, match="incomplete"):
        local.refresh_data(s)
    assert s.source_db.stat().st_mtime_ns == before
    assert local.load_state(s.data_dir)["asset_tag"] == asset_for(archive).tag


def test_offline_uses_the_existing_database_but_cannot_start_without_one(tmp_path, monkeypatch):
    def offline(*a, **k):
        raise OSError("no network")

    monkeypatch.setattr(local, "release_asset", offline)
    s = settings(tmp_path)
    with pytest.raises(RuntimeError, match="No football database"):
        local.refresh_data(s)
    s.source_db.write_bytes(b"")  # any existing file
    assert local.refresh_data(s) == {"changed": False, "offline": True, "asset_updated_at": None}


def test_an_export_without_tables_is_refused(tmp_path):
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w"):
        pass
    archive = tmp_path / "empty.tar.zst"
    archive.write_bytes(zstandard.ZstdCompressor().compress(buf.getvalue()))
    with pytest.raises(ValueError, match="no tables"):
        local.unpack(archive, tmp_path / "out")


def test_a_revision_is_republished_when_data_changes_or_its_deadline_passes(world, tmp_path):
    _, db_path, boot, fx = world
    svc = make_service(tmp_path, db_path, FakeFPL(boot, fx, {}))
    assert local.needs_publish(svc, False) == "no published revision yet"
    snapshots.publish(db_path, svc.settings.data_dir, svc.store, SEASON, boot, fx)
    assert local.needs_publish(svc, False) is None
    assert local.needs_publish(svc, True) == "the football data changed"
    nxt = next(e for e in boot["events"] if e["is_next"])
    after = datetime.fromisoformat(nxt["deadline_time"]) + timedelta(minutes=1)
    assert "deadline has passed" in local.needs_publish(svc, False, now=after.astimezone(UTC))


def test_pruning_keeps_the_newest_and_the_current_revision(tmp_path):
    snaps = tmp_path / "snapshots"
    for i, name in enumerate(["r1", "r2", "r3"]):
        d = snaps / name
        d.mkdir(parents=True)
        t = time.time() - 100 + i
        os.utime(d, (t, t))
    (snaps / ".r4.123.tmp").mkdir()
    removed = local.prune_revisions(tmp_path, keep={"r1"}, max_keep=1)
    assert sorted(removed) == ["r2"]
    assert sorted(p.name for p in snaps.iterdir()) == ["r1", "r3"]


def test_the_local_command_is_wired_into_the_cli():
    import argparse

    from fpl_agent.web import cli

    parser = argparse.ArgumentParser()
    cli.add_parser(parser.add_subparsers(dest="cmd"))
    args = parser.parse_args(["web", "local", "--entry", "8700026", "--no-refresh", "--no-browser"])
    assert (args.action, args.entry, args.no_refresh, args.port, args.site_port) == (
        "local",
        8700026,
        True,
        8000,
        3000,
    )
