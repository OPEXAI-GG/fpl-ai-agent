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
        reload_gws=tuple(int(g) for g in args.reload_gws.split(",") if g.strip()),
        players=args.players,
        player_limit=args.limit,
        export=Path(args.export) if args.export else None,
        repairs=args.repairs and not args.no_repairs,
    )
    db = _open_db(args)
    result = run_update(db, opts)
    print(result.message())
    return result.exit_code


def backfill(args: argparse.Namespace) -> int:
    from pathlib import Path

    from fpl_agent.data import leagues
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
        repairs=args.repairs and not args.no_repairs,
    )
    db = _open_db(args)
    result = run_backfill(db, opts)
    print(result.message())
    return result.exit_code


_OPENED: list = []  # databases opened by this command; closed by main()


def _open_db(args: argparse.Namespace):
    from fpl_agent.db.database import FootballDB

    db = FootballDB(args.db)
    _OPENED.append(db)
    if db.created_tables:  # the one schema change an open makes, said out loud
        print(f"schema: created missing tables {db.created_tables} in {args.db}", file=sys.stderr)
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


def lessons_cmd(args: argparse.Namespace, settings: Settings) -> int:
    """Find the finished gameweek's mistakes, store the lessons the next plan reads."""
    from fpl_agent import lessons, notify

    entry = args.entry or settings.entry_id
    if entry is None:
        print("lessons skipped: no FPL_ENTRY_ID")
        return 0
    fpl = FPLClient()
    gw = args.gw
    if gw == 0:  # latest finished gameweek
        done = [e["id"] for e in fpl.bootstrap_raw().get("events", []) if e.get("finished")]
        if not done:
            print("lessons skipped: no finished gameweek yet")
            return 0
        gw = max(done)
    db = _open_db(args)
    text = lessons.run(db, fpl, int(args.season), gw, entry)
    _export(db, args)
    print(text)
    if args.notify:
        notify.send(text)
    return 0


def explain_cmd(args: argparse.Namespace) -> int:
    """Why a player projects as he does: fixture, minutes, expected points by source."""
    db = _open_db(args)
    season, gw = int(args.season), args.gw
    if not gw:  # 0 = the latest gameweek with a stored plan
        gw = (
            db.con.execute("SELECT max(gw) FROM projections WHERE season = ?", [season]).fetchone()[
                0
            ]
            or 0
        )
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


def send_cmd(args: argparse.Namespace) -> int:
    """Post a file to Discord: as the reply to a slash command when a token is given (it lasts
    15 minutes), else, or when that fails and --fallback is set, to NOTIFY_WEBHOOK."""
    import os
    from pathlib import Path

    from fpl_agent import notify

    path = Path(args.file)
    body = path.read_text(encoding="utf-8").strip() if path.is_file() else ""
    message = "\n\n".join(p for p in (args.header, body or args.empty) if p)
    app = os.environ.get("DISCORD_APP_ID", "")
    if args.discord_token and app:
        if notify.send(message, webhook=notify.interaction_url(app, args.discord_token)):
            return 0
        if not args.fallback:
            return 1
    return 0 if notify.send(message) else 1


_EVAL_LABEL = {
    True: "VALIDATED: eligible gameweeks only, excluded matches removed",
    False: "EXPLORATORY: not restricted to eligible gameweeks (see db-eligibility)",
}


def backtest_cmd(args: argparse.Namespace) -> int:
    from fpl_agent.model import backtest
    from fpl_agent.model.simulate import SimConfig

    db = _open_db(args)
    from fpl_agent.db.eligibility import NotEligible

    gws = list(range(args.from_gw, args.to_gw + 1))
    try:
        res = backtest.projection_backtest(db, int(args.season), gws, SimConfig(n=args.samples),
                                           validated=args.validated)  # fmt: skip
    except NotEligible as e:
        print(f"not validated: {e}")
        return 1
    print(_EVAL_LABEL[args.validated])
    if res.empty:
        print("no gameweeks could be back-tested (not enough history in the database)")
        return 2
    summary = backtest.summarize(res)
    print(summary.round(3).to_string(index=False))
    print(backtest.paired_comparison(res).round(3).to_string(index=False))
    print(json.dumps(backtest.beats_baselines(res), indent=2))
    if args.out:
        res.to_csv(args.out, index=False)
    return 0


def replay_cmd(args: argparse.Namespace) -> int:
    from fpl_agent.model import replay

    db = _open_db(args)
    cfg = replay.ReplayConfig(horizon=args.horizon, samples=args.samples,
                              use_chips=not args.no_chips)  # fmt: skip
    cfg = replay.tuned(cfg, args.set or [])
    if args.set:
        print("settings: " + ", ".join(args.set))
    from fpl_agent.db.eligibility import NotEligible

    gws = list(range(args.from_gw, (args.to_gw or 38) + 1))
    print(_EVAL_LABEL[args.validated])
    try:
        res = replay.replay_season(db, int(args.season), gws, cfg, progress=lambda w: print(
            f"GW{w.gw}: {w.points} points, {w.transfers} transfers, {w.hits} hits"
            + (f", {w.chip}" if w.chip else ""), flush=True), validated=args.validated)  # fmt: skip
    except NotEligible as e:
        print(f"not validated: {e}")
        return 1
    if not res.weeks:
        print("no gameweeks could be replayed (no points for that season in the database)")
        return 2
    print(res.summary())
    if args.out:
        res.frame().to_csv(args.out, index=False)
    return 0


def retro_cmd(args: argparse.Namespace, settings: Settings) -> int:
    from fpl_agent.model import replay, retro
    from fpl_agent.solver.chips import ChipConfig
    from fpl_agent.solver.planner import PlannerConfig

    entry = args.entry or settings.entry_id
    if entry is None:
        print("FPL_ENTRY_ID is not set: cannot read your team")
        return 2
    db = _open_db(args)
    cfg = replay.ReplayConfig(horizon=args.horizon, samples=args.samples,
                              use_chips=not args.no_chips, planner=PlannerConfig(),
                              chips=ChipConfig())  # fmt: skip
    cfg = replay.tuned(cfg, args.set or [])
    report = retro.run(db, int(args.season), args.gw, int(entry), cfg)
    print(report)
    if args.out:
        from pathlib import Path

        Path(args.out).write_text(report + "\n", encoding="utf-8")
    return 0


def coverage_cmd(args: argparse.Namespace, settings: Settings) -> int:
    """Alarm when the deadline is close and no run produced a plan (dry) or verified moves (live):
    catches a schedule that silently did not run. Exit 1 when the alarm is raised."""
    from fpl_agent import notify, runs
    from fpl_agent.data.calendar import next_event

    entry = args.entry or settings.entry_id
    ev = next_event(FPLClient().bootstrap())
    if ev is None:
        print("no upcoming gameweek")
        return 0
    db = _open_db(args)
    try:
        cov = runs.coverage(db, int(args.season), ev.id, entry, ev.deadline_time,
                            settings.is_live, alarm_hours=args.hours)  # fmt: skip
        key = f"coverage:{args.season}:{ev.id}"
        sent = db.con.execute(
            "SELECT 1 FROM ingest_state WHERE source = 'alarm' AND key = ?", [key]
        ).fetchone()
        print(cov.message)
        if cov.alarm and args.notify and not sent and notify.send(cov.message):
            db.con.execute("INSERT OR REPLACE INTO ingest_state VALUES ('alarm', ?, 'sent', '', "
                           "now())", [key])  # fmt: skip
            _export(db, args)
    finally:
        db.close()
    return 1 if cov.alarm else 0


def fit_translations_cmd(args: argparse.Namespace) -> int:
    """Fit league-strength translations from players seen in two competitions and store them.
    Run outside the plan (it is slow); plans made before the fit keep using what they had."""
    from fpl_agent.model import features, translation

    db = _open_db(args)
    try:
        pm = features.match_frame(db)
        table = translation.estimate(pm)
        n = translation.store(db, table) if not table.empty else 0
        print(f"stored {n} translation factors from {pm['player_id'].nunique()} players")
        if args.validate:
            print(json.dumps(translation.validate(pm), indent=2, default=str))
        _export(db, args)
    finally:
        db.close()
    return 0


def verify_export_cmd(args: argparse.Namespace) -> int:
    """Check an export folder against its manifest; non-zero when it must not be published."""
    from fpl_agent.db.database import missing_tables, verify_export

    problems = verify_export(args.dir)
    if args.complete and not problems:
        # a fresh export must hold every declared table (empty ones too); only an older export
        # being restored may lack tables added since
        problems = [f"{n}: declared table not in the export" for n in missing_tables(args.dir)]
    for p in problems:
        print(f"export problem: {p}")
    if not problems:
        print(f"export {args.dir} is complete and matches its manifest")
    return 1 if problems else 0


def db_schema_cmd(args: argparse.Namespace) -> int:
    """Read-only migration preflight: JSON of missing/extra tables and column differences.
    Exit 0 = current, 3 = an open would create tables or a migration is needed, 1 = no file."""
    from pathlib import Path

    from fpl_agent.db.database import schema_status

    if not Path(args.db).exists():
        print(json.dumps({"db": args.db, "error": "no such file"}))
        return 1
    st = schema_status(args.db)
    print(json.dumps({"db": args.db, **st}, indent=1))
    return 0 if st["current"] else 3


def _read_db(path: str):
    from fpl_agent.db.database import FootballDB

    return FootballDB(path, schema="read")  # read-only: a report never writes


def source_health_cmd(args: argparse.Namespace) -> int:
    """Per-source state (failed / stale / ok_zero / ok / never) and data checks, as JSON.
    Exit 0 = healthy, 1 = degraded, 2 = blocked (decisions must not rely on the data)."""
    from fpl_agent.db.source_health import report

    db = _read_db(args.db)
    try:
        r = report(db, int(args.season))
    finally:
        db.close()
    print(json.dumps(r, indent=1))
    return {"healthy": 0, "degraded": 1, "blocked": 2}[r["overall"]]


def eligibility_cmd(args: argparse.Namespace) -> int:
    """Which past gameweeks are genuine decision-time evidence (eligible), rebuilt
    (reconstructed) or missing (unavailable), and which matches are excluded, as JSON."""
    from fpl_agent.db.eligibility import manifest

    db = _read_db(args.db)
    try:
        m = manifest(db, int(args.season))
    finally:
        db.close()
    print(json.dumps(m, indent=1, default=str))
    return 0


def links_cmd(args: argparse.Namespace) -> int:
    """How much of a season's FPL playing time has FotMob detail joined, and who is unlinked
    and why, as JSON (read-only). `--propose`: the evidence links the conservative rule would
    accept or rejects, from evidence before `--cutoff` (also read-only: nothing is applied).
    `--apply` (optionally `--reviewed file`) writes the accepted links as one batch and
    `--undo BATCH` rolls one back; both need an explicit `--db`, never the default file.
    Exit 1 = refused, nothing changed."""
    from datetime import UTC, datetime
    from pathlib import Path

    from fpl_agent.db.link_audit import coverage as link_coverage

    cutoff = datetime.fromisoformat(args.cutoff) if args.cutoff else datetime.now(UTC)
    cutoff = cutoff.replace(tzinfo=None)
    if args.apply or args.undo:
        from fpl_agent.db import player_links
        from fpl_agent.db.database import FootballDB

        if not args.db:
            print("not applied: name the database with --db (links are never applied implicitly)")
            return 1
        db = FootballDB(args.db)
        try:
            if args.undo:
                n = player_links.undo(db, args.undo)
                r = {"batch_id": args.undo, "rolled_back": n}
            else:
                reviewed = None
                if args.reviewed:
                    reviewed = json.loads(Path(args.reviewed).read_text(encoding="utf-8"))
                    reviewed = reviewed.get("accepted", reviewed)
                r = player_links.apply(db, int(args.season), cutoff, reviewed)
        except player_links.LinkConflict as e:
            print(f"not applied: {e}")
            return 1
        finally:
            db.close()
        print(json.dumps(r, indent=1, default=str))
        return 0 if not args.undo or r["rolled_back"] else 1
    db = _read_db(args.db or "data_cache/football.duckdb")
    try:
        if args.propose:
            from fpl_agent.db.link_candidates import history

            r = history(db, int(args.season), cutoff)
        elif args.batches:
            from fpl_agent.db.player_links import batches

            r = [] if "player_links" in db.missing_tables else batches(db)
        else:
            r = link_coverage(db, int(args.season), limit=args.limit)
    finally:
        db.close()
    text = json.dumps(r, indent=1, default=str)
    if args.out:
        Path(args.out).write_text(text, encoding="utf-8")
        print(f"wrote {args.out}")
    else:
        print(text)
    return 0


def repair_undo_cmd(args: argparse.Namespace) -> int:
    """Without an id: the journal of automatic repairs (read only). With one: undo that repair
    exactly, or change nothing and exit 1 when the data changed since."""
    from fpl_agent.db.database import FootballDB, SchemaError
    from fpl_agent.db.quality import RepairConflict, rollback_repair

    if not args.repair_id:
        import duckdb

        con = duckdb.connect(args.db, read_only=True)
        try:
            rows = con.execute(
                "SELECT repair_id, table_name, action, count(*), any_value(reason), "
                "max(rolled_back_at) FROM repair_log GROUP BY ALL ORDER BY ALL"
            ).fetchall()
        except duckdb.CatalogException:
            rows = []
        finally:
            con.close()
        for rid, table, action, n, why, back in rows:
            state = f"rolled back {back}" if back else "active"
            print(f"{rid}  {table} {action} {n}  ({why}; {state})")
        if not rows:
            print("no journalled repairs")
        return 0
    try:
        db = FootballDB(args.db, schema="require")  # an undo never migrates the file
    except SchemaError as e:
        print(f"not undone: {e}")
        return 1
    try:
        restored = rollback_repair(db, args.repair_id)
    except RepairConflict as e:
        print(f"not undone: {e}")
        return 1
    finally:
        db.close()
    if not restored:
        print(f"{args.repair_id} was already undone; nothing changed")
        return 0
    print(f"undone {args.repair_id}: restored {restored}")
    return 0


def dashboard_cmd(args: argparse.Namespace) -> int:
    """The local dashboard on 127.0.0.1 (read-only views plus dry-only jobs)."""
    from pathlib import Path

    from fpl_agent.dashboard.server import serve

    serve(Path(args.db), args.port, Path(args.state_dir), args.allow_model_keys)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="fpl-agent")
    sub = parser.add_subparsers(dest="cmd", required=True)
    nc = sub.add_parser(
        "news-check", help="store dated public injury/news evidence (no FPL writes)"
    )
    nc.add_argument("--db", default="data_cache/football.duckdb")
    nc.add_argument("--import-dir", default=None)
    nc.add_argument("--season", default="2026")
    nc.add_argument("--player", action="append", default=[], help="watch a full player name")
    nc.add_argument("--url", action="append", default=[], help="extra City/BBC article URL")
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
    d.add_argument(
        "--reload-gws",
        default="",
        help="comma list of finished GWs to reload "
        "with --points (repairs history stored by older code; back up first)",
    )
    d.add_argument(
        "--repairs",
        action="store_true",
        help="apply needed automatic repair/quarantine changes (journalled; see repair-undo). "
        "Without it they are only probed and the run stops (exit 3) if one is needed",
    )
    d.add_argument("--no-repairs", action="store_true", help="the default; kept for old scripts")
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
    b.add_argument(
        "--repairs",
        action="store_true",
        help="apply needed automatic repair/quarantine changes (journalled; see repair-undo). "
        "Without it they are only probed and the run stops (exit 3) if one is needed",
    )
    b.add_argument("--no-repairs", action="store_true", help="the default; kept for old scripts")
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
    ls = sub.add_parser("lessons", help="find a gameweek's mistakes and store the lessons")
    ls.add_argument("--db", default="data_cache/football.duckdb")
    ls.add_argument("--import-dir", default=None)
    ls.add_argument("--season", default="2026")
    ls.add_argument("--entry", type=int, default=None, help="FPL team id (or FPL_ENTRY_ID)")
    ls.add_argument("--gw", type=int, default=0, help="0 = latest finished gameweek")
    ls.add_argument("--export", default=None, help="write the database as parquet here")
    ls.add_argument("--notify", action="store_true", help="send the lessons to NOTIFY_WEBHOOK")
    gs = sub.add_parser("gw-summary", help="how your team did in the last finished gameweek")
    gs.add_argument("--entry", type=int, default=None, help="FPL team id (or FPL_ENTRY_ID)")
    gs.add_argument("--gw", type=int, default=0, help="0 = latest finished gameweek")
    gs.add_argument("--notify", action="store_true", help="send it to NOTIFY_WEBHOOK")
    ex = sub.add_parser("explain", help="why players project as they do (needs a stored plan)")
    ex.add_argument("--db", default="data_cache/football.duckdb")
    ex.add_argument("--import-dir", default=None)
    ex.add_argument("--season", default="2026")
    ex.add_argument("--gw", type=int, default=0, help="0 = latest gameweek with a stored plan")
    ex.add_argument("--players", required=True, help="comma-separated name fragments")
    se = sub.add_parser("send", help="post a file to Discord (slash-command reply or webhook)")
    se.add_argument("--file", required=True)
    se.add_argument("--header", default="", help="line put above the file's text")
    se.add_argument("--empty", default="(nothing to report)", help="text when the file is empty")
    se.add_argument("--discord-token", default="", help="interaction token from the Discord bot")
    se.add_argument("--fallback", action="store_true", help="use NOTIFY_WEBHOOK if the reply fails")
    bt = sub.add_parser("backtest", help="point-in-time accuracy of the projection stack")
    bt.add_argument("--db", default="data_cache/football.duckdb")
    bt.add_argument("--import-dir", default=None)
    bt.add_argument("--season", default="2025")
    bt.add_argument("--from-gw", type=int, default=10)
    bt.add_argument("--to-gw", type=int, default=20)
    bt.add_argument("--samples", type=int, default=600)
    bt.add_argument("--out", default=None)
    bt.add_argument(
        "--validated",
        action="store_true",
        help="eligible gameweeks only, excluded matches removed (db-eligibility)",
    )
    rp = sub.add_parser("replay", help="run the agent through a past season, scored on real points")
    rp.add_argument("--db", default="data_cache/football.duckdb")
    rp.add_argument("--import-dir", default=None)
    rp.add_argument("--season", default="2025")
    rp.add_argument("--from-gw", type=int, default=1)
    rp.add_argument("--to-gw", type=int, default=None, help="last gameweek (default: the last)")
    rp.add_argument("--horizon", type=int, default=4)
    rp.add_argument("--samples", type=int, default=300)
    rp.add_argument("--no-chips", action="store_true")
    rp.add_argument("--set", action="append", help="override a setting, e.g. hit_buffer=1.5")
    rp.add_argument("--out", default=None)
    rp.add_argument(
        "--validated",
        action="store_true",
        help="eligible gameweeks only, excluded matches removed (db-eligibility)",
    )
    rt = sub.add_parser(
        "retro", help="what the agent would decide for a gameweek, against your team"
    )
    rt.add_argument("--db", default="data_cache/football.duckdb")
    rt.add_argument("--import-dir", default=None)
    rt.add_argument("--season", default="2026")
    rt.add_argument("--gw", type=int, required=True)
    rt.add_argument("--entry", type=int, default=None, help="FPL team id (or FPL_ENTRY_ID)")
    rt.add_argument("--horizon", type=int, default=8)
    rt.add_argument("--samples", type=int, default=1000)
    rt.add_argument("--no-chips", action="store_true")
    rt.add_argument("--set", action="append", help="override a setting, e.g. hit_buffer=1.5")
    rt.add_argument("--out", default=None)
    db_ = sub.add_parser("dashboard", help="local dashboard on http://127.0.0.1 (dry-only jobs)")
    db_.add_argument("--db", default="data_cache/football.duckdb")
    db_.add_argument("--port", type=int, default=8765)
    db_.add_argument("--state-dir", default="data_cache/dashboard",
                     help="where dashboard jobs, settings and plan files are kept")  # fmt: skip
    db_.add_argument("--allow-model-keys", action="store_true",
                     help="pass ANTHROPIC_API_KEY / ODDS_API_KEY to dry plans (costs)")  # fmt: skip
    cc = sub.add_parser("coverage-check", help="alarm if no good run before the deadline")
    cc.add_argument("--db", default="data_cache/football.duckdb")
    cc.add_argument("--import-dir", default=None)
    cc.add_argument("--season", default="2026")
    cc.add_argument("--entry", type=int, default=None)
    cc.add_argument("--hours", type=float, default=3.0, help="alarm this close to the deadline")
    cc.add_argument("--notify", action="store_true")
    cc.add_argument("--export", default=None)
    ft = sub.add_parser("fit-translations", help="fit and store league-strength factors")
    ft.add_argument("--db", default="data_cache/football.duckdb")
    ft.add_argument("--import-dir", default=None)
    ft.add_argument("--export", default=None)
    ft.add_argument("--validate", action="store_true", help="also print the leave-one-out check")
    ve = sub.add_parser("verify-export", help="check an export folder against its manifest")
    ve.add_argument("dir")
    ve.add_argument(
        "--complete",
        action="store_true",
        help="also require every declared table (used before publishing)",
    )
    ds = sub.add_parser("db-schema", help="read-only: does a database match the declared schema")
    ds.add_argument("--db", default="data_cache/football.duckdb")
    sh = sub.add_parser("source-health", help="read-only: each source's state and data checks")
    sh.add_argument("--db", default="data_cache/football.duckdb")
    sh.add_argument("--season", type=int, default=2026)
    el = sub.add_parser("db-eligibility",
                        help="read-only: which past gameweeks are genuine evidence")  # fmt: skip
    el.add_argument("--db", default="data_cache/football.duckdb")
    el.add_argument("--season", type=int, default=2026)
    lk = sub.add_parser("db-links", help="FPL players and minutes linked to FotMob (read-only "
                        "unless --apply/--undo)")  # fmt: skip
    lk.add_argument("--db", default=None, help="default data_cache/football.duckdb (reads only)")
    lk.add_argument("--season", type=int, default=2026)
    lk.add_argument("--limit", type=int, default=None, help="cap the unlinked list")
    lk.add_argument("--propose", action="store_true",
                    help="propose evidence links (read-only; nothing is applied)")  # fmt: skip
    lk.add_argument("--cutoff", default=None, help="ISO time: use only evidence before it")
    lk.add_argument("--out", default=None, help="write the JSON here instead of printing")
    lk.add_argument("--apply", action="store_true", help="write the accepted links as a batch")
    lk.add_argument("--reviewed", default=None, help="with --apply: only these reviewed pairs")
    lk.add_argument("--undo", default=None, metavar="BATCH", help="roll back one batch")
    lk.add_argument("--batches", action="store_true", help="list applied batches")
    ru = sub.add_parser(
        "repair-undo", help="list journalled repairs, or undo one exactly (back up first)"
    )
    ru.add_argument("--db", default="data_cache/football.duckdb")
    ru.add_argument("repair_id", nargs="?", help="omit to list the journal")
    args = parser.parse_args(argv)
    try:
        return _dispatch(args)
    finally:
        while _OPENED:
            try:
                _OPENED.pop().close()
            except Exception:  # noqa: BLE001 - already closed
                pass


def _dispatch(args: argparse.Namespace) -> int:
    if args.cmd == "news-check":
        from fpl_agent.news import monitor

        db = _open_db(args)
        names = args.player or [
            r[0]
            for r in db.con.execute(
                "SELECT DISTINCT p.name FROM fpl_players_gw f JOIN players p USING(player_id) "
                "WHERE f.season = ? AND f.gw = (SELECT max(gw) FROM fpl_players_gw "
                "WHERE season = ?) AND f.status = 'd'",
                [int(args.season)] * 2,
            ).fetchall()
        ]
        rows = monitor(db, names, urls=args.url)
        print(
            json.dumps(
                {
                    "coverage": "City official news plus BBC Football; advisory only",
                    "observations": len(rows),
                    "failures": sum(r["state"] == "failed" for r in rows),
                    "matching": [r for r in rows if r["players"]],
                },
                ensure_ascii=True,
            )
        )
        return 0 if any(r["state"] != "failed" for r in rows) else 2
    if args.cmd == "verify-export":
        return verify_export_cmd(args)
    if args.cmd == "repair-undo":
        return repair_undo_cmd(args)
    if args.cmd == "db-schema":
        return db_schema_cmd(args)
    if args.cmd == "source-health":
        return source_health_cmd(args)
    if args.cmd == "db-eligibility":
        return eligibility_cmd(args)
    if args.cmd == "db-links":
        return links_cmd(args)
    settings = Settings.from_env()
    if args.cmd == "dashboard":
        return dashboard_cmd(args)
    if args.cmd == "fit-translations":
        return fit_translations_cmd(args)
    if args.cmd == "coverage-check":
        return coverage_cmd(args, settings)
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
    if args.cmd == "lessons":
        return lessons_cmd(args, settings)
    if args.cmd == "gw-summary":
        return gw_summary_cmd(args, settings)
    if args.cmd == "backtest":
        return backtest_cmd(args)
    if args.cmd == "send":
        return send_cmd(args)
    if args.cmd == "replay":
        return replay_cmd(args)
    if args.cmd == "retro":
        return retro_cmd(args, settings)
    if args.cmd == "understat":
        return understat(settings, args.season, args.players, args.limit)
    return 2


if __name__ == "__main__":
    sys.exit(main())
