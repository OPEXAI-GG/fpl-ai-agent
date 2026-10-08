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
    from pathlib import Path

    from fpl_agent.data import leagues
    from fpl_agent.db.database import FootballDB
    from fpl_agent.pipeline import UpdateOptions, run_update

    fotmob = (
        tuple(leagues.LEAGUES)
        if args.fotmob == "all"
        else tuple(c for c in (args.fotmob or "").split(",") if c)
    )
    opts = UpdateOptions(
        season=int(args.season),
        fotmob=fotmob,
        fotmob_matches=args.fotmob_matches,
        understat_leagues=tuple(c for c in args.understat_leagues.split(",") if c),
        football_data=tuple(d for d in (args.football_data or "").split(",") if d),
        points=args.points,
        players=args.players,
        player_limit=args.limit,
        export=Path(args.export) if args.export else None,
    )
    db = FootballDB(args.db)
    if args.import_dir:
        db.import_parquet(args.import_dir)
    result = run_update(db, opts)
    print(result.message())
    return result.exit_code


def backfill(args: argparse.Namespace) -> int:
    from pathlib import Path

    from fpl_agent.data import leagues
    from fpl_agent.db.database import FootballDB
    from fpl_agent.pipeline import BackfillOptions, run_backfill

    comps = tuple(c for c in args.competitions.split(",") if c) or tuple(leagues.LEAGUES)
    opts = BackfillOptions(
        start=args.start,
        end=args.end,
        competitions=comps,
        fotmob_matches=args.fotmob_matches,
        sources=tuple(s for s in args.sources.split(",") if s),
        export=Path(args.export) if args.export else None,
    )
    db = FootballDB(args.db)
    if args.import_dir:
        db.import_parquet(args.import_dir)
    result = run_backfill(db, opts)
    print(result.message())
    return result.exit_code


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
    d.add_argument("--import-dir", default=None, help="load previously exported parquet first")
    d.add_argument("--fotmob", default="", help="'all' or comma list of competition ids")
    d.add_argument("--fotmob-matches", type=int, default=60, help="match pages per competition")
    d.add_argument("--understat-leagues", default="EPL", help="comma list, e.g. EPL,La_liga")
    b = sub.add_parser("backfill", help="fill history from 2017/18 (resumable, bounded per run)")
    b.add_argument("--db", default="data_cache/football.duckdb")
    b.add_argument("--start", type=int, default=2017)
    b.add_argument("--end", type=int, default=2026)
    b.add_argument("--competitions", default="", help="comma list of competition ids (default all)")
    b.add_argument("--sources", default="football-data,understat,fotmob,vaastav")
    b.add_argument("--fotmob-matches", type=int, default=400)
    b.add_argument("--export", default=None)
    b.add_argument("--import-dir", default=None)
    args = parser.parse_args(argv)
    settings = Settings.from_env()
    if args.cmd == "snapshot":
        return snapshot(settings)
    if args.cmd == "db-update":
        return db_update(args)
    if args.cmd == "backfill":
        return backfill(args)
    if args.cmd == "understat":
        return understat(settings, args.season, args.players, args.limit)
    return 2


if __name__ == "__main__":
    sys.exit(main())
