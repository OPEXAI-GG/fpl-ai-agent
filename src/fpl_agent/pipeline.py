"""The weekly/daily database update, with every source guarded, validated against its contract
and written in a transaction. A broken source is skipped and reported; the database is only
exported when the quality checks pass. Every run records each source's outcome in
`source_attempts`, failed and blocked runs included."""

from __future__ import annotations

import re
import time
import uuid
from dataclasses import dataclass
from pathlib import Path

import duckdb
import httpx
import pandas as pd

from fpl_agent import contracts, notify
from fpl_agent.data import leagues
from fpl_agent.data.calendar import next_event
from fpl_agent.data.fotmob import FotMobClient
from fpl_agent.data.fpl_client import FPLClient
from fpl_agent.data.id_map import build_id_map
from fpl_agent.data.schemas import Bootstrap
from fpl_agent.data.understat import UnderstatClient
from fpl_agent.db import (
    ingest_football_data,
    ingest_fotmob,
    ingest_fpl,
    ingest_understat,
    ingest_vaastav,
)
from fpl_agent.db.database import FootballDB
from fpl_agent.db.quality import (
    Issue,
    blocked_writes,
    errors,
    format_issues,
    quarantine,
    repair,
    run_checks,
)
from fpl_agent.errors import SourceFormatError, SourceUnavailable
from fpl_agent.health import HealthReport, SourceResult, guard

OK, CRITICAL_SOURCE_FAILED, BAD_DATA = 0, 2, 3


@dataclass
class UpdateOptions:
    season: int = 2026
    football_data: tuple[str, ...] = ("E0", "E1")
    points: bool = False
    players: bool = False
    player_limit: int = 25
    export: Path | None = None
    fotmob: tuple[str, ...] = ()  # competition ids, e.g. ("ENG1", "ENG2"); () = skip FotMob
    fotmob_matches: int = 60  # match pages per competition per run (the rest wait for next run)
    understat_leagues: tuple[str, ...] = ("EPL",)
    # automatic repair/quarantine changes are opt-in: by default they are only probed (on a
    # copy) and any change they would make blocks the run (exit 3) without being applied
    repairs: bool = False


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
    fm: FotMobClient | None = None,
) -> UpdateResult:
    fpl = fpl or FPLClient()
    us = us or UnderstatClient()
    http = http or http_client()
    fm = fm or FotMobClient()
    report = HealthReport()
    season = opts.season
    fixed = _repair_step(db, report, opts.repairs)
    if fixed is None or errors(fixed):  # a needed repair is disabled, or it failed: load nothing
        return _finish(db, report, fixed or [], BAD_DATA, opts, alert)

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
    league = None
    for code in opts.understat_leagues:
        name = "understat:league" if code == "EPL" else f"understat:{code}"
        got = guard(report, name, lambda c=code: understat_league(db, us, c, season))
        if code == "EPL":
            league = got
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
        guard(
            report,
            f"football-data:{div}",
            lambda d=div: load_football_data(db, http, d, season),
        )

    # 4. FotMob: Championship, feeder leagues, European cups (match-level xG, lineups, defensive
    # actions). Optional per competition: one failing league does not stop the others.
    if opts.fotmob:
        ing = ingest_fotmob.FotMobIngestor(db)
        for comp in opts.fotmob:
            guard(
                report,
                f"fotmob:{comp}",
                lambda c=comp: fotmob_competition(db, ing, fm, c, season, opts.fotmob_matches),
            )

    # 5. Quality gate. Errors mean the data must not be used or exported.
    issues, code = _quality_gate(db, report, fixed, opts.repairs)
    return _finish(db, report, issues, code, opts, alert)


def _repair_step(db: FootballDB, report: HealthReport, apply: bool) -> list[Issue] | None:
    """Repairs before loading: applied (journalled, see `repair-undo`) only when `apply`;
    otherwise probed on a copy, and a change they would make is an error. An exception is a
    failed critical step in the report (and so in `source_attempts`), never a silent stop.
    Returns the issues, or None when the step failed."""
    return guard(report, "step:repair",
                 lambda: repair(db) if apply else blocked_writes(db, repair, "repair"),
                 critical=True)  # fmt: skip


def _quality_gate(
    db: FootballDB, report: HealthReport, fixed: list[Issue], apply: bool
) -> tuple[list[Issue], int]:
    """Quarantine (applied only when `apply`, else probed like the repairs) and the checks,
    each guarded like `_repair_step`. Any error, or a failed step, means exit 3: no export."""
    held = guard(report, "step:quarantine",
                 lambda: quarantine(db) if apply else blocked_writes(db, quarantine, "quarantine"),
                 critical=True)  # fmt: skip
    found = guard(report, "step:checks", lambda: run_checks(db), critical=True)
    issues = fixed + (held or []) + (found or [])
    failed = held is None or found is None
    return issues, BAD_DATA if failed or errors(issues) else OK


def understat_league(db: FootballDB, us: UnderstatClient, code: str, season: int):
    league = us.league_data(str(season), code)
    contracts.require(contracts.understat_league(league), f"understat {code}")
    with db.transaction():
        ingest_understat.load_league(db, league, season, code)
    return league


def fotmob_competition(
    db: FootballDB,
    ing: ingest_fotmob.FotMobIngestor,
    fm: FotMobClient,
    comp: str,
    season: int,
    limit: int,
) -> int:
    """Fixtures for one competition, then the match pages of finished matches not yet stored.
    Resumable: matches already done (or known to have no detail) are skipped, so a long backfill
    simply continues on the next run. Returns the number of match pages stored."""
    lg = leagues.get(comp)
    raw = fm.league(lg.fotmob_id, lg.fotmob_season(season))
    contracts.require(contracts.fotmob_league(raw), f"fotmob {comp}")
    with db.transaction():
        ing.load_league(raw, lg, season)
    todo = db.con.execute(
        "SELECT i.source_id FROM matches m JOIN match_ids i USING (match_id) "
        "WHERE i.source = 'fotmob' AND m.competition_id = ? AND m.season = ? AND m.finished "
        "AND i.source_id NOT IN (SELECT key FROM ingest_state WHERE source = 'fotmob') "
        "ORDER BY m.match_date DESC LIMIT ?",
        [comp, season, limit],
    ).fetchall()
    done = failed = 0
    for (mid,) in todo:
        try:
            page = fm.match(mid)
            contracts.require(contracts.fotmob_match(page), f"fotmob match {mid}")
            with db.transaction():
                n = ing.load_match(page, lg, season)
                status = "done" if n else "no_detail"
                db.con.execute(
                    "INSERT OR REPLACE INTO ingest_state VALUES ('fotmob', ?, ?, ?, ?)",
                    [mid, status, f"{comp} {season}", ingest_fotmob.now()],
                )
            done += 1
        except (SourceUnavailable, SourceFormatError):
            failed += 1
    if todo and failed > max(1, len(todo) // 4):
        raise SourceFormatError(f"fotmob {comp}: {failed} of {len(todo)} match pages failed")
    return done


def load_vaastav(db: FootballDB, http: httpx.Client, season: int) -> int:
    resp = http.get(ingest_vaastav.url(season))
    if resp.status_code >= 400:
        raise SourceUnavailable(f"vaastav {season}: HTTP {resp.status_code}")
    contracts.require(ingest_vaastav.problems(resp.text), f"vaastav {season}")
    positions = None
    if not ingest_vaastav.has_position(resp.text):  # 2016/17-2019/20 files omit it
        raw = http.get(ingest_vaastav.players_raw_url(season))
        if raw.status_code >= 400:
            raise SourceUnavailable(f"vaastav players_raw {season}: HTTP {raw.status_code}")
        positions = ingest_vaastav.positions_from_players_raw(raw.text)
    with db.transaction():
        return ingest_vaastav.load_csv(db, resp.text, season, positions)


def load_football_data(db: FootballDB, http: httpx.Client, div: str, season: int) -> int:
    resp = http.get(ingest_football_data.url(div, season))
    if resp.status_code >= 400:
        raise SourceUnavailable(f"football-data {div} {season}: HTTP {resp.status_code}")
    contracts.require(contracts.football_data_csv(resp.text, div), f"football-data {div}")
    with db.transaction():
        return ingest_football_data.load_csv(db, resp.text, div, season)


# ---- backfill ------------------------------------------------------------------------------
FD_DIVS = {"ENG1": "E0", "ENG2": "E1", "ENG3": "E2"}
US_LEAGUES = {v: k for k, v in ingest_understat.COMPETITIONS.items()}  # ENG1 -> EPL ...
EARLIEST_SEASON = 2017  # first season with consistent xG; the plan's backfill start


@dataclass
class BackfillOptions:
    start: int = EARLIEST_SEASON
    end: int = 2026  # inclusive; the current season is also refreshed by the weekly update
    competitions: tuple[str, ...] = tuple(leagues.LEAGUES)
    fotmob_matches: int = 400  # match pages per competition-season per run
    sources: tuple[str, ...] = ("football-data", "understat", "fotmob", "vaastav")
    export: Path | None = None
    max_minutes: float | None = None  # stop starting new steps after this, and save the progress
    repairs: bool = False  # see UpdateOptions.repairs


_clock = time.monotonic  # the backfill's time budget (replaced in tests)


def _marked(db: FootballDB, key: str) -> bool:
    return bool(
        db.con.execute(
            "SELECT 1 FROM ingest_state WHERE source = 'backfill' AND key = ?", [key]
        ).fetchone()
    )


def _mark(db: FootballDB, key: str) -> None:
    db.con.execute(
        "INSERT OR REPLACE INTO ingest_state VALUES ('backfill', ?, 'done', '', ?)",
        [key, ingest_fotmob.now()],
    )


def run_backfill(
    db: FootballDB,
    opts: BackfillOptions,
    fm: FotMobClient | None = None,
    us: UnderstatClient | None = None,
    http: httpx.Client | None = None,
    alert: bool = True,
) -> UpdateResult:
    """Fill history season by season: first the quick sources (vaastav points and prices,
    football-data results, Understat xG) for every season, then FotMob match pages.

    Resumable and idempotent: finished season-sources are remembered, FotMob match pages
    continue where the previous run stopped (`fotmob_matches` per competition-season per run),
    and a failing source/season is skipped and reported. With `max_minutes` the run stops
    starting new steps once the time is up and still saves what it has, so a run that would
    otherwise hit the job's time limit never loses its work."""
    fm, us, http = fm or FotMobClient(), us or UnderstatClient(), http or http_client()
    report = HealthReport()
    fixed = _repair_step(db, report, opts.repairs)
    if fixed is None or errors(fixed):  # a needed repair is disabled, or it failed: load nothing
        return _finish(db, report, fixed or [], BAD_DATA, UpdateOptions(export=opts.export),
                       alert, "backfill")  # fmt: skip
    ing = ingest_fotmob.FotMobIngestor(db)
    current = opts.end
    t0 = _clock()
    stopped: list[str] = []

    def out_of_time(key: str) -> bool:
        if opts.max_minutes is None or (_clock() - t0) / 60 < opts.max_minutes:
            return False
        stopped.append(key)
        return True

    def once(key: str, fn):
        """Run a step once for a finished season; the current season is always refreshed."""
        season = int(key.rsplit(":", 1)[1])
        if season < current and _marked(db, key):
            return None
        if out_of_time(key):
            return None
        out = guard(report, key, fn)
        if out is not None and season < current:
            _mark(db, key)
        return out

    seasons = range(opts.start, opts.end + 1)
    for season in seasons:
        if "vaastav" in opts.sources and season >= 2016 and season < opts.end:
            once(f"vaastav:{season}", lambda s=season: load_vaastav(db, http, s))
        for comp in opts.competitions:
            if "football-data" in opts.sources and comp in FD_DIVS:
                div = FD_DIVS[comp]
                once(
                    f"football-data:{div}:{season}",
                    lambda d=div, s=season: load_football_data(db, http, d, s),
                )
            if "understat" in opts.sources and comp in US_LEAGUES:
                code = US_LEAGUES[comp]
                once(
                    f"understat:{code}:{season}",
                    lambda c=code, s=season: understat_league(db, us, c, s),
                )
    if "fotmob" in opts.sources:
        for season in seasons:
            for comp in opts.competitions:
                key = f"fotmob:{comp}:{season}"
                if out_of_time(key):
                    continue
                guard(report, key, lambda c=comp, s=season: fotmob_competition(
                    db, ing, fm, c, s, opts.fotmob_matches))  # fmt: skip
    if stopped:
        report.results.append(SourceResult(
            "time budget", True, error=f"stopped after {opts.max_minutes:.0f} min with "
            f"{len(stopped)} steps left (next: {stopped[0]}); the next run continues"))  # fmt: skip
    issues, code = _quality_gate(db, report, fixed, opts.repairs)
    return _finish(db, report, issues, code, UpdateOptions(export=opts.export), alert, "backfill")


_QUERY = re.compile(r"\?\S*")  # a URL's query string may carry a key: never stored


def record_attempts(db: FootballDB, report: HealthReport, job: str, code: int) -> str | None:
    """Store every attempted source's outcome in `source_attempts`, in a transaction of its own
    (see the table's note in schema.py). Returns the attempt id, or None when there was nothing
    to record. A failure to record is added to the report; it never changes the exit code."""
    if not report.results:
        return None
    attempt = uuid.uuid4().hex
    rows = pd.DataFrame([{
        "attempt_id": attempt, "seq": i, "job": job, "source": r.name, "ok": r.ok,
        "critical": r.critical, "rows": r.rows,
        "error": _QUERY.sub("?…", r.error)[:300] if r.error else None,
        "started_at": r.started_at, "finished_at": r.finished_at, "exit_code": code,
    } for i, r in enumerate(report.results)])  # fmt: skip
    try:
        with db.transaction():
            db.append("source_attempts", rows)
    except duckdb.Error as exc:
        report.results.append(SourceResult("record:attempts", False,
                                           error=f"{type(exc).__name__}"))  # fmt: skip
        return None
    return attempt


def _finish(db, report, issues, code, opts, alert, what: str = "data update") -> UpdateResult:
    result = UpdateResult(code, report, issues)
    record_attempts(db, report, what, code)
    if code == OK and opts.export:
        db.export_parquet(opts.export)
    if alert and (report.failed or code != OK):
        notify.send(f"FPL agent {what} needs attention (exit {code}):\n{result.message()}")
    return result
