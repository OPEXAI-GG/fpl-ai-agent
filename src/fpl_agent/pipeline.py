"""The weekly/daily database update, with every source guarded, validated against its contract
and written in a transaction. A broken source is skipped and reported; the database is only
exported when the quality checks pass."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import httpx

from fpl_agent import contracts, notify
from fpl_agent.data.calendar import next_event
from fpl_agent.data.fpl_client import FPLClient
from fpl_agent.data.id_map import build_id_map
from fpl_agent.data.schemas import Bootstrap
from fpl_agent.data.understat import UnderstatClient
from fpl_agent.db import ingest_football_data, ingest_fpl, ingest_understat
from fpl_agent.db.database import FootballDB
from fpl_agent.db.quality import Issue, errors, format_issues, run_checks
from fpl_agent.errors import SourceFormatError, SourceUnavailable
from fpl_agent.health import HealthReport, guard

OK, CRITICAL_SOURCE_FAILED, BAD_DATA = 0, 2, 3


@dataclass
class UpdateOptions:
    season: int = 2026
    football_data: tuple[str, ...] = ("E0", "E1")
    points: bool = False
    players: bool = False
    player_limit: int = 25
    export: Path | None = None


@dataclass
class UpdateResult:
    exit_code: int
    report: HealthReport
    issues: list[Issue]

    def message(self) -> str:
        return f"{self.report.summary()}\nquality: {format_issues(self.issues)}"


def http_client() -> httpx.Client:
    return httpx.Client(timeout=30, follow_redirects=True)


def run_update(
    db: FootballDB,
    opts: UpdateOptions,
    fpl: FPLClient | None = None,
    us: UnderstatClient | None = None,
    http: httpx.Client | None = None,
    alert: bool = True,
) -> UpdateResult:
    fpl = fpl or FPLClient()
    us = us or UnderstatClient()
    http = http or http_client()
    report = HealthReport()
    season = opts.season

    # 1. FPL bootstrap is critical: without it there is nothing to decide on.
    def pull_bootstrap():
        raw = fpl.bootstrap_raw()
        contracts.require(contracts.fpl_bootstrap(raw), "fpl bootstrap")
        return raw

    raw = guard(report, "fpl:bootstrap", pull_bootstrap, critical=True)
    if raw is None:
        return _finish(db, report, [], CRITICAL_SOURCE_FAILED, opts, alert)
    nxt = next_event(Bootstrap.model_validate(raw))
    gw = nxt.id if nxt else 38

    def load_bootstrap():
        with db.transaction():
            return ingest_fpl.load_bootstrap(db, raw, season, gw)

    guard(report, "fpl:players", load_bootstrap, critical=True)

    def load_fixtures():
        fixtures = fpl.fixtures_raw()
        contracts.require(contracts.fpl_fixtures(fixtures), "fpl fixtures")
        with db.transaction():
            return ingest_fpl.load_fixtures(db, fixtures, raw, season)

    guard(report, "fpl:fixtures", load_fixtures)

    if opts.points:
        have = {
            r[0]
            for r in db.con.execute(
                "SELECT DISTINCT gw FROM fpl_points_gw WHERE season = ?", [season]
            ).fetchall()
        }
        for ev in raw["events"]:
            if not ev["finished"] or ev["id"] in have:
                continue

            def load_points(gw_id=ev["id"]):
                live = fpl.live_raw(gw_id)
                contracts.require(contracts.fpl_live(live), f"fpl live gw{gw_id}")
                with db.transaction():
                    return ingest_fpl.load_live(db, live, season, gw_id)

            guard(report, f"fpl:points gw{ev['id']}", load_points)

    # 2. Understat: xG and shots.
    def pull_league():
        league = us.league_data(str(season))
        contracts.require(contracts.understat_league(league), "understat league")
        with db.transaction():
            ingest_understat.load_league(db, league, season)
        return league

    league = guard(report, "understat:league", pull_league)
    if league and opts.players:
        canon = dict(
            db.con.execute(
                "SELECT source_id::INTEGER, player_id FROM player_ids WHERE source = 'fpl'"
            ).fetchall()
        )
        id_map = build_id_map(raw, league["players"])
        todo = id_map.dropna(subset=["fpl_id"]).head(opts.player_limit)
        failed = 0

        def load_players():
            nonlocal failed
            done = 0
            for r in todo.itertuples():
                try:
                    data = us.player_data(r.understat_id)
                    contracts.require(
                        contracts.understat_player(data), f"understat {r.understat_id}"
                    )
                    with db.transaction():
                        ingest_understat.load_player(db, data, canon.get(int(r.fpl_id)))
                    done += 1
                except (SourceUnavailable, SourceFormatError):
                    failed += 1
            if todo.shape[0] and failed > todo.shape[0] // 4:
                raise SourceFormatError(f"{failed} of {todo.shape[0]} player pulls failed")
            return done

        guard(report, "understat:players", load_players)

    # 3. football-data.co.uk: Championship / PL results, xG, odds.
    for div in opts.football_data:

        def load_div(div=div):
            resp = http.get(ingest_football_data.url(div, season))
            if resp.status_code >= 400:
                raise SourceUnavailable(f"football-data {div}: HTTP {resp.status_code}")
            contracts.require(contracts.football_data_csv(resp.text, div), f"football-data {div}")
            with db.transaction():
                return ingest_football_data.load_csv(db, resp.text, div, season)

        guard(report, f"football-data:{div}", load_div)

    # 4. Quality gate. Errors mean the data must not be used or exported.
    issues = run_checks(db)
    code = BAD_DATA if errors(issues) else OK
    return _finish(db, report, issues, code, opts, alert)


def _finish(db, report, issues, code, opts, alert) -> UpdateResult:
    result = UpdateResult(code, report, issues)
    if code == OK and opts.export:
        db.export_parquet(opts.export)
    if alert and (report.failed or code != OK):
        notify.send(f"FPL agent data update needs attention (exit {code}):\n{result.message()}")
    return result
