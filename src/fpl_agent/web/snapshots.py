"""Immutable read-only copies of the football database for the website.

The engine's working database (`data_cache/football.duckdb`) has one writer: the data refresh.
The website never opens it. Instead the refresh publishes a *revision*: a copy of the file,
taken while holding a read-only connection (which a writer's lock would refuse), verified and
moved into place atomically, together with the FPL bootstrap and fixtures it was built from.
Any number of API and worker processes then read that copy with read-only connections.

A revision's cutoff is the moment it was copied: everything in the file was observed before
then, so a forecast built from it uses no later information. The newest FPL squad snapshot it
holds is recorded beside it (`fpl_snapshot_at`).
"""

from __future__ import annotations

import hashlib
import json
import shutil
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from fpl_agent import pit
from fpl_agent.db.database import FootballDB
from fpl_agent.web.store import Store

DB_NAME = "football.duckdb"


@dataclass(frozen=True)
class Revision:
    id: str
    path: Path  # directory holding football.duckdb, bootstrap.json, fixtures.json, meta.json
    meta: dict

    @property
    def db_path(self) -> Path:
        return self.path / DB_NAME

    @property
    def cutoff(self) -> datetime:
        return datetime.fromisoformat(self.meta["cutoff"])

    def open(self) -> FootballDB:
        return FootballDB.read_only(self.db_path)

    def captured(self, name: str):
        p = self.path / f"{name}.json"
        return json.loads(p.read_text()) if p.exists() else None


def describe(db: FootballDB, season: int) -> dict:
    """What a database holds that matters to a forecast: the newest FPL snapshot (the
    cutoff), the gameweeks with a squad snapshot and with official points, and row counts."""
    snap = db.con.execute(
        "SELECT max(snapshot_at) FROM fpl_players_gw WHERE season = ?", [season]
    ).fetchone()[0]
    gws = [
        r[0]
        for r in db.con.execute(
            "SELECT DISTINCT gw FROM fpl_players_gw WHERE season = ? ORDER BY gw", [season]
        ).fetchall()
    ]
    pts = [
        r[0]
        for r in db.con.execute(
            "SELECT DISTINCT gw FROM fpl_points_gw WHERE season = ? ORDER BY gw", [season]
        ).fetchall()
    ]
    counts = {
        t: db.con.execute(f"SELECT count(*) FROM {t}").fetchone()[0]
        for t in (
            "matches",
            "appearances",
            "fpl_players_gw",
            "fpl_points_gw",
            "projections",
            "field_ownership",
        )
    }
    return {
        "season": season,
        "fpl_snapshot_at": snap.isoformat() if snap else None,
        "snapshot_gws": gws,
        "points_gws": pts,
        "counts": counts,
    }


def publish(
    source: Path,
    root: Path,
    store: Store,
    season: int,
    bootstrap: dict | None = None,
    fixtures: list | None = None,
) -> Revision:
    """Copy the working database into a new revision and make it current."""
    source = Path(source)
    if not source.exists():
        raise FileNotFoundError(f"no football database at {source}: restore or refresh it first")
    src = FootballDB.read_only(source)  # refused while a writer holds the file
    try:
        meta = describe(src, season)
        if not meta["fpl_snapshot_at"]:
            raise ValueError(f"the database has no FPL snapshot for season {season}")
        digest = hashlib.sha256(json.dumps(meta, sort_keys=True).encode()).hexdigest()[:10]
        stamp = datetime.fromisoformat(meta["fpl_snapshot_at"]).strftime("%Y%m%dT%H%M%S")
        rid = f"r{stamp}-{digest}"
        content = dict(meta)
        final = Path(root) / "snapshots" / rid
        if not (final / DB_NAME).exists():
            tmp = Path(root) / "snapshots" / f".{rid}.{int(time.time())}.tmp"
            tmp.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, tmp / DB_NAME)
            if bootstrap is not None:
                (tmp / "bootstrap.json").write_text(json.dumps(bootstrap))
            if fixtures is not None:
                (tmp / "fixtures.json").write_text(json.dumps(fixtures))
            check = FootballDB.read_only(tmp / DB_NAME)
            try:
                if describe(check, season) != content:
                    raise RuntimeError("the copied database differs from its source")
            finally:
                check.close()
            meta["cutoff"] = pit.utc(datetime.now(UTC)).isoformat()
            (tmp / "meta.json").write_text(json.dumps(meta, indent=2))
            if final.exists():
                shutil.rmtree(final)
            tmp.rename(final)
    finally:
        src.close()
    meta = json.loads((final / "meta.json").read_text())
    store.add_revision(rid, str(final), meta)
    return Revision(rid, final, meta)


def current(store: Store) -> Revision | None:
    r = store.current_revision()
    if r is None or not (Path(r["path"]) / DB_NAME).exists():
        return None
    return Revision(r["id"], Path(r["path"]), r["meta"])


__all__ = ["Revision", "publish", "current", "describe"]
