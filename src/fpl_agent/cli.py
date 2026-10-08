"""Command line entry point: `fpl-agent snapshot`."""

from __future__ import annotations

import argparse
import json
import sys

from fpl_agent.config import Settings
from fpl_agent.data.calendar import next_event
from fpl_agent.data.fpl_client import FPLClient
from fpl_agent.data.id_map import build_id_map, coverage
from fpl_agent.data.players import fixtures_frame, players_frame
from fpl_agent.data.schemas import Bootstrap
from fpl_agent.data.store import SnapshotStore
from fpl_agent.data.understat import UnderstatClient, player_frames


def snapshot(settings: Settings) -> int:
    client = FPLClient()
    raw = client.bootstrap_raw()
    fixtures = client.fixtures_raw()
    bs = Bootstrap.model_validate(raw)
    nxt = next_event(bs)
    gw = nxt.id if nxt else None
    store = SnapshotStore(settings.snapshot_dir)
    store.write("bootstrap", gw, raw)
    store.write("fixtures", gw, fixtures)
    store.write_frame("players", players_frame(raw), gw)
    store.write_frame("fixtures", fixtures_frame(fixtures), gw)
    print(f"snapshot saved for GW{gw}: {len(bs.elements)} players, {len(fixtures)} fixtures")
    return 0


def understat(settings: Settings, season: str, with_players: bool, limit: int | None) -> int:
    import json

    import pandas as pd

    fpl_raw = FPLClient().bootstrap_raw()
    nxt = next_event(Bootstrap.model_validate(fpl_raw))
    gw = nxt.id if nxt else None
    store = SnapshotStore(settings.snapshot_dir)
    client = UnderstatClient()
    league = client.league_data(season)
    store.write("understat_league", gw, league)
    id_map = build_id_map(fpl_raw, league["players"])
    store.write_frame("id_map", id_map, gw)
    cov = coverage(id_map, fpl_raw)
    print(json.dumps({k: v for k, v in cov.items() if k != "missing"}))
    if cov["missing"]:
        print("unmapped players with minutes:", ", ".join(cov["missing"]))
    if with_players:
        ids = id_map.dropna(subset=["fpl_id"])["understat_id"].tolist()[:limit]
        matches, shots = [], []
        for uid in ids:
            m, s = player_frames(client.player_data(uid))
            matches.append(m)
            shots.append(s)
        store.write_frame("understat_matches", pd.concat(matches, ignore_index=True), gw)
        store.write_frame("understat_shots", pd.concat(shots, ignore_index=True), gw)
        print(f"stored match + shot history for {len(ids)} players")
    return 0


def db_update(args: argparse.Namespace) -> int:
    import httpx

    from fpl_agent.db import ingest_football_data, ingest_fpl, ingest_understat
    from fpl_agent.db.database import FootballDB

    season = int(args.season)
    db = FootballDB(args.db)
    fpl = FPLClient()
    raw, fixtures = fpl.bootstrap_raw(), fpl.fixtures_raw()
    nxt = next_event(Bootstrap.model_validate(raw))
    gw = nxt.id if nxt else 38
    ingest_fpl.load_bootstrap(db, raw, season, gw)
    ingest_fpl.load_fixtures(db, fixtures, raw, season)
    for ev in raw["events"]:
        if ev["finished"] and args.points:
            ingest_fpl.load_live(db, fpl.live_raw(ev["id"]), season, ev["id"])
    us = UnderstatClient()
    league = us.league_data(str(season))
    ingest_understat.load_league(db, league, season)
    id_map = build_id_map(raw, league["players"])
    if args.players:
        fpl_to_canon = dict(
            db.con.execute(
                "SELECT source_id::INTEGER, player_id FROM player_ids WHERE source = 'fpl'"
            ).fetchall()
        )
        rows = id_map.dropna(subset=["fpl_id"]).head(args.limit)
        for r in rows.itertuples():
            ingest_understat.load_player(
                db, us.player_data(r.understat_id), fpl_to_canon.get(int(r.fpl_id))
            )
    for div in args.football_data.split(",") if args.football_data else []:
        resp = httpx.get(ingest_football_data.url(div, season), follow_redirects=True, timeout=30)
        resp.raise_for_status()
        ingest_football_data.load_csv(db, resp.text, div, season)
    print(json.dumps(db.counts()))
    if args.export:
        db.export_parquet(args.export)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="fpl-agent")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("snapshot", help="pull public FPL data and store a point-in-time snapshot")
    u = sub.add_parser("understat", help="pull Understat xG data and map players to FPL ids")
    u.add_argument("--season", default="2026", help="start year, e.g. 2026 for 2026/27")
    u.add_argument(
        "--players", action="store_true", help="also pull per-player match + shot history"
    )
    u.add_argument("--limit", type=int, default=None, help="cap players pulled (testing)")
    d = sub.add_parser("db-update", help="update the football database from all sources")
    d.add_argument("--db", default="data_cache/football.duckdb")
    d.add_argument("--season", default="2026")
    d.add_argument("--football-data", default="E0,E1", help="comma list of divisions")
    d.add_argument("--points", action="store_true", help="also load official FPL points per GW")
    d.add_argument("--players", action="store_true", help="also load Understat player histories")
    d.add_argument("--limit", type=int, default=25, help="max players for --players")
    d.add_argument("--export", default=None, help="write parquet files to this directory")
    args = parser.parse_args(argv)
    settings = Settings.from_env()
    if args.cmd == "snapshot":
        return snapshot(settings)
    if args.cmd == "db-update":
        return db_update(args)
    if args.cmd == "understat":
        return understat(settings, args.season, args.players, args.limit)
    return 2


if __name__ == "__main__":
    sys.exit(main())
