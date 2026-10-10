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
        max_minutes=args.max_minutes,
    )
    db = FootballDB(args.db)
    if args.import_dir:
        db.import_parquet(args.import_dir)
    result = run_backfill(db, opts)
    print(result.message())
    return result.exit_code


def _open_db(args: argparse.Namespace):
    from fpl_agent.db.database import FootballDB

    db = FootballDB(args.db)
    if getattr(args, "import_dir", None):
        db.import_parquet(args.import_dir)
    return db


def status(settings: Settings) -> int:
    """What the agent will do on its next run, and which settings are missing (never values)."""
    import os

    live = ["FPL_REFRESH_TOKEN", "FPL_TOKEN_URL", "FPL_CLIENT_ID", "FPL_ENTRY_ID"]
    missing = [k for k in live if not os.environ.get(k)]
    if not settings.enabled:
        print("Kill switch is ON (AGENT_ENABLED=false): the agent will do nothing.")
    elif settings.is_live:
        print("Mode: LIVE - the agent will make moves on your FPL team and then report them.")
        if missing:
            print("Cannot act yet; missing: " + ", ".join(missing) + ". Runs will stop.")
            return 2
    else:
        print("Mode: DRY - the agent only reports; it never logs in to FPL or changes your team.")
    for k in ("ANTHROPIC_API_KEY", "ODDS_API_KEY", "NOTIFY_WEBHOOK"):
        print(f"{k}: {'set' if os.environ.get(k) else 'not set'}")
    return 0


def token_check(settings: Settings, notify_on_fail: bool = True) -> int:
    """Sign in to FPL once and read the team: keeps the rotating token in use (it lapses when
    unused for hours) and warns on Discord while there is still time to paste a fresh one."""
    from fpl_agent import notify
    from fpl_agent.execution.auth import AuthError, TokenExchange
    from fpl_agent.execution.fpl_writer import FPLWriter

    if not settings.is_live or settings.entry_id is None:
        print("token check skipped: not in live mode or no FPL_ENTRY_ID")
        return 0
    try:
        tokens = TokenExchange.from_env().exchange()
        FPLWriter(tokens.access, settings.entry_id).my_team()
    except AuthError as exc:
        why = str(exc)
    except Exception as exc:  # noqa: BLE001  (a failed read is a failed check)
        why = f"FPL sign-in worked but your team could not be read ({type(exc).__name__})"
    else:
        print("FPL sign-in OK: token rotated and saved")
        return 0
    msg = ("FPL token check failed: " + why + ". Paste a fresh refresh token into the "
           "FPL_REFRESH_TOKEN secret before the next deadline, then keep FPL closed in the "
           "browser until the next run.")  # fmt: skip
    print(msg)
    if notify_on_fail:
        notify.send(msg)
    return 2


def gw_summary_cmd(args: argparse.Namespace, settings: Settings) -> int:
    """How your team did in the latest finished gameweek; sent to Discord with --notify."""
    from fpl_agent import notify
    from fpl_agent.gw_summary import gw_summary

    entry = args.entry or settings.entry_id
    if entry is None:
        print("gw-summary skipped: no FPL_ENTRY_ID")
        return 0
    text = gw_summary(FPLClient(), entry, args.gw or None)
    print(text)
    if args.notify:
        notify.send(text)
    return 0


def _export(db, args: argparse.Namespace) -> None:
    if getattr(args, "export", None):
        db.export_parquet(args.export)


def plan(args: argparse.Namespace, settings: Settings) -> int:
    """Weekly decision. Dry unless AGENT_MODE=live; never needs credentials in dry mode."""
    from pathlib import Path

    from fpl_agent.agent import RunOptions, run_weekly

    opts = RunOptions(
        season=int(args.season), horizon=args.horizon,
        entry_id=args.entry or settings.entry_id, force=args.force,
        use_chips=not args.no_chips, samples=args.samples,
        out_dir=Path(args.out) if args.out else None, notify=args.notify,
    )  # fmt: skip
    db = _open_db(args)
    res = run_weekly(db, settings, opts)
    _export(db, args)
    print(res.report)
    return res.exit_code


def review_cmd(args: argparse.Namespace) -> int:
    from fpl_agent import review

    db = _open_db(args)
    season = int(args.season)
    if args.gw == 0:  # latest gameweek with official points
        row = db.con.execute(
            "SELECT max(gw) FROM fpl_points_gw WHERE season = ?", [season]).fetchone()  # fmt: skip
        args.gw = int(row[0] or 0)
    out = {
        "prediction": review.prediction_review(db, season, args.gw),
        "decision": review.decision_review(db, season, args.gw),
        "season": review.summarize(db, season),
    }
    _export(db, args)
    print(json.dumps(out, indent=2, default=str))
    return 0


def explain_cmd(args: argparse.Namespace) -> int:
    """Why a player projects as he does: fixture, minutes, expected points by source."""
    db = _open_db(args)
    season, gw = int(args.season), args.gw
    run = db.con.execute(
        "SELECT run_id FROM projections WHERE season = ? AND gw = ? "
        "ORDER BY created_at DESC LIMIT 1", [season, gw]).fetchone()  # fmt: skip
    if not run:
        print(f"no stored projection for GW{gw}: run the plan first")
        return 1
    teams = dict(db.con.execute("SELECT team_id, name FROM teams").fetchall())
    for needle in [n.strip() for n in args.players.split(",") if n.strip()]:
        rows = db.con.execute(
            "SELECT p.player_id, p.name, pr.xpts, pr.detail, f.team_id, f.status, f.chance_next "
            "FROM players p JOIN projections pr ON pr.player_id = p.player_id AND pr.run_id = ? "
            "AND pr.gw = ? LEFT JOIN fpl_players_gw f ON f.player_id = p.player_id "
            "AND f.season = ? AND f.gw = ? WHERE lower(p.name) LIKE ? "
            "QUALIFY row_number() OVER (PARTITION BY p.player_id ORDER BY f.snapshot_at DESC) = 1",
            [run[0], gw, season, gw, f"%{needle.lower()}%"],
        ).fetchall()  # fmt: skip
        for _pid, name, xpts, detail, team_id, status, chance in rows:
            d = json.loads(detail)
            fx = db.con.execute(
                "SELECT home_team_id, away_team_id, kickoff FROM matches WHERE season = ? "
                "AND matchday = ? AND competition_id = 'ENG1' AND (home_team_id = ? "
                "OR away_team_id = ?)", [season, gw, team_id, team_id]).fetchall()  # fmt: skip
            opp = [f"{teams.get(h)} v {teams.get(a)} ({k})" for h, a, k in fx] or ["no fixture"]
            comps = ", ".join(f"{k} {v:.2f}" for k, v in sorted(d.get("components", {}).items()))
            print(f"{name} [{teams.get(team_id)}] status={status} chance={chance}")
            print(f"  fixture: {'; '.join(opp)}")
            print(
                f"  expected points {xpts:.2f}  minutes {d.get('exp_minutes', 0):.0f}  "
                f"P(blank) {d.get('p_zero', 0):.0%}  P(10+) {d.get('p10', 0):.0%}  "
                f"range {d.get('q10', 0):.0f}-{d.get('q90', 0):.0f}"
            )
            print(f"  by source: {comps}")
    return 0


def backtest_cmd(args: argparse.Namespace) -> int:
    from fpl_agent.model import backtest
    from fpl_agent.model.simulate import SimConfig

    db = _open_db(args)
    gws = list(range(args.from_gw, args.to_gw + 1))
    res = backtest.projection_backtest(db, int(args.season), gws, SimConfig(n=args.samples))
    if res.empty:
        print("no gameweeks could be back-tested (not enough history in the database)")
        return 2
    summary = backtest.summarize(res)
    print(summary.round(3).to_string(index=False))
    print(json.dumps(backtest.beats_baselines(summary), indent=2))
    if args.out:
        res.to_csv(args.out, index=False)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="fpl-agent")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("status", help="show dry/live mode and which settings are missing")
    tc = sub.add_parser("token-check", help="sign in to FPL once; alert if the token is dead")
    tc.add_argument("--no-notify", action="store_true")
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
    b.add_argument(
        "--max-minutes",
        type=float,
        default=None,
        help="stop starting new steps after this long, and save the progress",
    )
    b.add_argument("--export", default=None)
    b.add_argument("--import-dir", default=None)
    pl = sub.add_parser(
        "plan", help="weekly decision: transfers, chip, XI, captain (dry by default)"
    )
    pl.add_argument("--db", default="data_cache/football.duckdb")
    pl.add_argument("--import-dir", default=None)
    pl.add_argument("--season", default="2026")
    pl.add_argument("--entry", type=int, default=None, help="FPL team id (or FPL_ENTRY_ID)")
    pl.add_argument("--horizon", type=int, default=8)
    pl.add_argument("--samples", type=int, default=1000)
    pl.add_argument("--force", action="store_true", help="ignore the deadline-window gate")
    pl.add_argument("--no-chips", action="store_true")
    pl.add_argument("--export", default=None, help="write the database as parquet here")
    pl.add_argument("--out", default=None, help="write gw<N>.md and gw<N>.json here")
    pl.add_argument("--notify", action="store_true", help="send the report to NOTIFY_WEBHOOK")
    rv = sub.add_parser("review", help="score a finished gameweek: predictions and decisions")
    rv.add_argument("--db", default="data_cache/football.duckdb")
    rv.add_argument("--import-dir", default=None)
    rv.add_argument("--season", default="2026")
    rv.add_argument("--export", default=None, help="write the database as parquet here")
    rv.add_argument("--gw", type=int, default=0, help="0 = latest gameweek with points")
    gs = sub.add_parser("gw-summary", help="how your team did in the last finished gameweek")
    gs.add_argument("--entry", type=int, default=None, help="FPL team id (or FPL_ENTRY_ID)")
    gs.add_argument("--gw", type=int, default=0, help="0 = latest finished gameweek")
    gs.add_argument("--notify", action="store_true", help="send it to NOTIFY_WEBHOOK")
    ex = sub.add_parser("explain", help="why players project as they do (needs a stored plan)")
    ex.add_argument("--db", default="data_cache/football.duckdb")
    ex.add_argument("--import-dir", default=None)
    ex.add_argument("--season", default="2026")
    ex.add_argument("--gw", type=int, required=True)
    ex.add_argument("--players", required=True, help="comma-separated name fragments")
    bt = sub.add_parser("backtest", help="point-in-time accuracy of the projection stack")
    bt.add_argument("--db", default="data_cache/football.duckdb")
    bt.add_argument("--import-dir", default=None)
    bt.add_argument("--season", default="2025")
    bt.add_argument("--from-gw", type=int, default=10)
    bt.add_argument("--to-gw", type=int, default=20)
    bt.add_argument("--samples", type=int, default=600)
    bt.add_argument("--out", default=None)
    args = parser.parse_args(argv)
    settings = Settings.from_env()
    if args.cmd == "explain":
        return explain_cmd(args)
    if args.cmd == "status":
        return status(settings)
    if args.cmd == "token-check":
        return token_check(settings, not args.no_notify)
    if args.cmd == "snapshot":
        return snapshot(settings)
    if args.cmd == "db-update":
        return db_update(args)
    if args.cmd == "backfill":
        return backfill(args)
    if args.cmd == "plan":
        return plan(args, settings)
    if args.cmd == "review":
        return review_cmd(args)
    if args.cmd == "gw-summary":
        return gw_summary_cmd(args, settings)
    if args.cmd == "backtest":
        return backtest_cmd(args)
    if args.cmd == "understat":
        return understat(settings, args.season, args.players, args.limit)
    return 2


if __name__ == "__main__":
    sys.exit(main())
