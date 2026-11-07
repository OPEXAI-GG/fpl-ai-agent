"""The shared forecast: one simulation for every visitor, per data revision and model setup.

A forecast is keyed by everything that can change it: the data revision (and so its cutoff),
the gameweeks, the sample count and seed, the model and feature versions, the feature modes
and the position calibration in force. Two visitors asking for the same gameweek share it, and
a new revision or a learning-state change produces a new key rather than a silent overwrite.

What the planner receives is what the weekly agent gives it before its owner-specific steps:
the raw simulation with the global position calibration applied. The owner's lessons memory
(built from one manager's decisions) is deliberately *not* applied: it is not evidence about
football and must not shape other managers' plans.

Artifacts are plain files (numpy arrays, parquet tables and a JSON manifest) written to a
temporary directory and renamed into place, so a reader never sees half a forecast.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

from fpl_agent import agent, pit
from fpl_agent.model import calibration, overlays, project
from fpl_agent.model.simulate import SimConfig
from fpl_agent.rules import Rules
from fpl_agent.web.snapshots import Revision

DEFAULT_SAMPLES = 1000
DEFAULT_HORIZON = 8
SEED = SimConfig().seed


@dataclass
class Shared:
    """A loaded forecast and the shared, non-personal tables built with it."""

    key: str
    proj: project.Projection
    manifest: dict
    players: pd.DataFrame  # one row per projected player: identity, FPL stats, availability
    ownership: dict[int, float]  # percent of managers owning (canonical id)
    eo: dict[int, float]  # top-10k effective ownership as a fraction; empty when unavailable
    names: dict[int, str]
    pool: pd.DataFrame  # the snapshot's squad rows for the first gameweek


def config_for(
    rev: Revision,
    season: int,
    gw: int,
    horizon: int = DEFAULT_HORIZON,
    samples: int = DEFAULT_SAMPLES,
) -> dict:
    """The full configuration of a forecast, read from the revision's learning state."""
    db = rev.open()
    try:
        modes, fac, version = agent.active_config(db, season, gw)
    finally:
        db.close()
    gws = list(range(gw, min(gw + horizon, 39)))
    return {
        "revision": rev.id,
        "cutoff": rev.meta["cutoff"],
        "season": season,
        "gws": gws,
        "samples": int(samples),
        "seed": SEED,
        "model": pit.MODEL_VERSION,
        "features_version": pit.FEATURE_VERSION,
        "modes": modes,
        "calibration": {k: round(float(v), 6) for k, v in sorted(fac.items())},
        "learning_version": version,
    }


def key_of(cfg: dict) -> str:
    return "fc-" + hashlib.sha256(json.dumps(cfg, sort_keys=True).encode()).hexdigest()[:16]


def build(rev: Revision, cfg: dict, out_root: Path, progress=None) -> tuple[str, Path, dict]:
    """Simulate, calibrate and write the forecast for `cfg`. Returns (key, path, manifest)."""
    key = key_of(cfg)
    final = Path(out_root) / key
    if (final / "manifest.json").exists():
        return key, final, json.loads((final / "manifest.json").read_text())
    t0 = time.time()
    rules = Rules.nearest(cfg["season"])
    cutoff = pit.utc(cfg["cutoff"])
    db = rev.open()
    try:
        gw = cfg["gws"][0]
        pool = project.current_pool(db, cfg["season"], gw, as_of=cutoff)
        if pool.empty:
            raise RuntimeError(f"the data revision has no squad snapshot for GW{gw}")
        if progress:
            progress("simulating matches", 0.1)
        on = sorted(f for f, m in cfg["modes"].items() if m == "on")
        ovs = overlays.build(on, db, {})
        proj = project.build_projection(
            db,
            cfg["season"],
            cfg["gws"],
            rules=rules,
            pool=pool,
            pool_gw=gw,
            cutoff=cutoff,
            cfg=SimConfig(n=cfg["samples"], seed=cfg["seed"]),
            overlays=ovs,
        )
        if progress:
            progress("calibrating and saving", 0.85)
        if cfg["calibration"]:
            calibration.apply(proj, cfg["calibration"])
        names = dict(db.con.execute("SELECT player_id, name FROM players").fetchall())
        own, eo, own_source = _ownership(db, cfg["season"], gw, pool)
        teams = _teams(db, pool, rev)
    finally:
        db.close()
    manifest = {
        **cfg,
        "key": key,
        "created_at": time.time(),
        "build_seconds": round(time.time() - t0, 1),
        "features_on": on,
        "limitations": proj.limitations,
        "flags": proj.flags,
        "ownership_source": own_source,
        "issued": "raw simulation x position calibration",
        "excluded": ["owner lessons memory (single-manager evidence)"],
    }
    tmp = Path(out_root) / f".{key}.{int(time.time() * 1000)}.tmp"
    tmp.mkdir(parents=True, exist_ok=True)
    np.save(tmp / "points.npy", proj.points)
    np.save(tmp / "minutes.npy", proj.minutes)
    np.savez_compressed(tmp / "components.npz", **proj.components)
    proj.meta.to_parquet(tmp / "meta.parquet")
    if proj.minutes_table is not None:
        mt = proj.minutes_table.drop(
            columns=[c for c in proj.minutes_table.columns if proj.minutes_table[c].dtype == object]
        )
        mt.to_parquet(tmp / "minutes_table.parquet")
    _player_table(pool, names, teams).to_parquet(tmp / "players.parquet")
    (tmp / "fixtures.json").write_text(json.dumps({str(k): v for k, v in proj.fixtures.items()}))
    (tmp / "ownership.json").write_text(json.dumps({"owned": own, "eo": eo}))
    (tmp / "teams.json").write_text(json.dumps(teams))
    (tmp / "manifest.json").write_text(json.dumps(manifest, indent=2, default=str))
    if final.exists():
        shutil.rmtree(final)
    tmp.rename(final)
    return key, final, manifest


def _ownership(db, season: int, gw: int, pool: pd.DataFrame):
    """Shared ownership inputs: the stored top-10k sample when the refresh took one, else FPL's
    overall ownership (and no effective ownership). Never read live per visitor."""
    pid = dict(zip(pool["fpl_id"].astype(int), pool["player_id"].astype(int), strict=True))
    f = db.query(
        "SELECT fpl_id, owned, eo, sample FROM field_ownership WHERE season = ? AND gw = ?",
        [season, gw],
    )
    if not f.empty:
        own = {str(pid[int(r.fpl_id)]): float(r.owned) for r in f.itertuples() if r.fpl_id in pid}
        eo = {str(pid[int(r.fpl_id)]): float(r.eo) / 100 for r in f.itertuples() if r.fpl_id in pid}
        return own, eo, f"top-10k sample of {int(f['sample'].iloc[0])} managers"
    o = pool[["player_id", "owned"]].dropna()
    own = {str(int(p)): float(v) for p, v in zip(o["player_id"], o["owned"], strict=True)}
    return own, {}, "FPL overall ownership (no top-10k sample in this data revision)"


def _teams(db, pool: pd.DataFrame, rev: Revision) -> dict[str, dict]:
    """Canonical team id -> FPL id, name and short name (from the revision's bootstrap)."""
    boot = rev.captured("bootstrap") or {}
    fpl_teams = {t["id"]: t for t in boot.get("teams", [])}
    out: dict[str, dict] = {}
    for r in pool.itertuples():
        raw = json.loads(r.raw) if isinstance(r.raw, str) else {}
        ft = raw.get("team")
        if r.team_id in out or ft is None:
            continue
        t = fpl_teams.get(ft, {})
        out[str(int(r.team_id))] = {
            "fpl_id": ft,
            "name": t.get("name") or str(ft),
            "short": t.get("short_name") or str(ft),
        }
    missing = [int(t) for t in pool["team_id"].unique() if str(int(t)) not in out]
    if missing:
        rows = db.query(
            "SELECT team_id, name FROM teams WHERE team_id IN (SELECT unnest(?))", [missing]
        )
        for r in rows.itertuples():
            out[str(int(r.team_id))] = {"fpl_id": None, "name": r.name, "short": r.name[:3]}
    return out


STAT_FIELDS = (
    "minutes",
    "starts",
    "total_points",
    "points_per_game",
    "form",
    "goals_scored",
    "assists",
    "expected_goals",
    "expected_assists",
    "expected_goal_involvements",
    "expected_goals_per_90",
    "expected_assists_per_90",
    "clean_sheets",
    "defensive_contribution",
    "defensive_contribution_per_90",
    "bonus",
    "saves",
    "penalties_order",
    "corners_and_indirect_freekicks_order",
    "direct_freekicks_order",
    "ep_next",
    "selected_by_percent",
)


def _num(v):
    try:
        return None if v is None or v == "" else float(v)
    except (TypeError, ValueError):
        return None


def _player_table(pool: pd.DataFrame, names: dict[int, str], teams: dict) -> pd.DataFrame:
    rows = []
    for r in pool.itertuples():
        raw = json.loads(r.raw) if isinstance(r.raw, str) else {}
        rows.append(
            {
                "player_id": int(r.player_id),
                "fpl_id": int(r.fpl_id),
                "name": raw.get("web_name") or names.get(int(r.player_id)) or str(r.player_id),
                "full_name": " ".join(
                    x for x in (raw.get("first_name"), raw.get("second_name")) if x
                )
                or None,
                "team_id": int(r.team_id),
                "team": teams.get(str(int(r.team_id)), {}).get("short"),
                "position": r.position,
                "price": int(r.price),
                "status": r.status,
                "chance": None if pd.isna(r.chance) else float(r.chance),
                "news": r.news or "",
                "news_added": raw.get("news_added"),
                **{f: _num(raw.get(f)) for f in STAT_FIELDS},
            }
        )
    return pd.DataFrame(rows)


# ---- loading -----------------------------------------------------------------------------------
_CACHE: OrderedDict[str, Shared] = OrderedDict()
_LOCK = threading.Lock()
CACHE_SIZE = 2


def load(path: Path) -> Shared:
    """A forecast from its directory, cached in memory (the newest two)."""
    path = Path(path)
    key = path.name
    with _LOCK:
        if key in _CACHE:
            _CACHE.move_to_end(key)
            return _CACHE[key]
    manifest = json.loads((path / "manifest.json").read_text())
    meta = pd.read_parquet(path / "meta.parquet")
    comps = dict(np.load(path / "components.npz"))
    mt = (
        pd.read_parquet(path / "minutes_table.parquet")
        if (path / "minutes_table.parquet").exists()
        else None
    )
    fixtures = {
        int(k): [tuple(x) for x in v]
        for k, v in json.loads((path / "fixtures.json").read_text()).items()
    }
    proj = project.Projection(
        run_id=key,
        season=manifest["season"],
        gws=list(manifest["gws"]),
        meta=meta,
        points=np.load(path / "points.npy", mmap_mode="r"),
        minutes=np.load(path / "minutes.npy", mmap_mode="r"),
        components=comps,
        fixtures=fixtures,
        cutoff=pit.utc(manifest["cutoff"]),
        flags=manifest.get("flags", {}),
        limitations=manifest.get("limitations", []),
        minutes_table=mt,
    )
    players = pd.read_parquet(path / "players.parquet")
    own = json.loads((path / "ownership.json").read_text())
    shared = Shared(
        key,
        proj,
        manifest,
        players,
        {int(k): v for k, v in own["owned"].items()},
        {int(k): v for k, v in own["eo"].items()},
        dict(zip(players["player_id"].astype(int), players["name"], strict=True)),
        players,
    )
    with _LOCK:
        _CACHE[key] = shared
        while len(_CACHE) > CACHE_SIZE:
            _CACHE.popitem(last=False)
    return shared


def clear_cache() -> None:
    with _LOCK:
        _CACHE.clear()


__all__ = ["Shared", "config_for", "key_of", "build", "load", "clear_cache"]
