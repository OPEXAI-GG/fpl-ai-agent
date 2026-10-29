"""Per-source health from the attempt record and the data itself, for people and for consumers.

Everything is evaluated as of `at`: only attempts that had finished by then count (an attempt
with no `finished_at` is still running and is reported as `unfinished`, never as an outcome;
one that finished after `at` is invisible). The newest attempt is the one with the latest
`finished_at`, ties broken by `attempt_id` then `seq` (both increase within a run), and its
rows and error are reported from that same row. States keep apart what a reader must not
confuse:

* `failed`: the newest attempt failed (feed unavailable or rejected): its data was not updated;
* `stale`: the newest attempt succeeded, but longer ago than the limit;
* `ok_zero`: the newest attempt succeeded and genuinely found no rows (an actual zero);
* `ok`: succeeded recently with rows (or with a row count the source does not report);
* `never`: an expected critical source with no finished attempt on record.

Data checks do not depend on the attempt record (older files have none): the newest FPL
snapshot taken by `at` and its age; finished Premier League gameweeks without official points
and finished matches without FotMob detail (these two reflect the stored state, which has no
history). `overall` is `blocked` when a critical source failed or is stale, or the snapshot is
stale or missing (decisions must not use the data); `degraded` when an optional source failed
or is stale, a critical one has no record (unknown, not failed), or data is incomplete; else
`healthy`. Read-only.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from fpl_agent.db.database import FootballDB

CRITICAL = ("fpl:bootstrap", "fpl:fixtures", "fpl:players")
STALE_AFTER = timedelta(hours=26)  # a daily update plus slack
SNAPSHOT_STALE_AFTER = timedelta(hours=6)  # as the dashboard


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def _iso(t: datetime | None) -> str | None:
    return t.isoformat() if t else None


_ORDER = "ORDER BY finished_at DESC, attempt_id DESC, seq DESC"


def sources(db: FootballDB, at: datetime) -> list[dict[str, Any]]:
    if "source_attempts" in getattr(db, "missing_tables", []):
        return []
    rows = db.con.execute(
        "WITH a AS (SELECT * FROM source_attempts WHERE finished_at IS NOT NULL AND "
        "finished_at <= ?), "
        "latest AS (SELECT * FROM a QUALIFY row_number() OVER "
        f"(PARTITION BY source {_ORDER}) = 1), "
        "agg AS (SELECT source, max(finished_at) FILTER (WHERE ok) AS good, "
        "max(finished_at) FILTER (WHERE NOT ok) AS bad, bool_or(critical) AS crit FROM a "
        "GROUP BY source) "
        "SELECT l.source, l.ok, l.rows, l.error, l.finished_at, g.good, g.bad, g.crit, "
        "(SELECT count(*) FROM a x WHERE x.source = l.source AND NOT x.ok AND "
        "x.finished_at > coalesce(g.good, TIMESTAMP '1900-01-01')), "
        "(SELECT count(*) FROM source_attempts u WHERE u.source = l.source AND "
        "u.finished_at IS NULL AND u.started_at <= ?) "
        "FROM latest l JOIN agg g USING (source) ORDER BY l.source", [at, at]
    ).fetchall()  # fmt: skip
    out = []
    for src, ok, n, err, last, good, bad, critical, fails, running in rows:
        if not ok:
            state = "failed"
        elif at - last > STALE_AFTER:
            state = "stale"
        elif n == 0:
            state = "ok_zero"
        else:
            state = "ok"
        out.append({"source": src, "state": state, "critical": bool(critical),
                    "last_attempt_at": _iso(last), "last_success_at": _iso(good),
                    "last_failure_at": _iso(bad), "failures_since_success": fails,
                    "last_rows": n, "last_error": None if ok else err,
                    "unfinished": running})  # fmt: skip
    seen = {r["source"] for r in out}
    out += [{"source": c, "state": "never", "critical": True, "last_attempt_at": None,
             "last_success_at": None, "last_failure_at": None, "failures_since_success": 0,
             "last_rows": None, "last_error": None, "unfinished": 0}
            for c in CRITICAL if c not in seen]  # fmt: skip
    return out


def critical_failures_after(rep: dict[str, Any], snapshot_at: datetime | None) -> list[str]:
    """Critical sources whose newest attempt failed after `snapshot_at` (the snapshot a plan
    uses): that snapshot predates a failed refresh and nothing newer validates it. A failure
    older than the snapshot is not one (a later pull succeeded). Unknown snapshot: every
    failed critical source counts."""
    out = []
    for s in rep["sources"]:
        if s["critical"] and s["state"] == "failed":
            bad = datetime.fromisoformat(s["last_failure_at"])
            if snapshot_at is None or bad > snapshot_at:
                out.append(f"{s['source']} failed at {s['last_failure_at']}: {s['last_error']}")
    return out


def data_checks(db: FootballDB, season: int, at: datetime) -> dict[str, Any]:
    snap = db.con.execute(
        "SELECT max(snapshot_at) FROM fpl_players_gw WHERE season = ? AND snapshot_at <= ?",
        [season, at],
    ).fetchone()[0]
    age = None if snap is None else (at - snap).total_seconds() / 3600
    no_points = [r[0] for r in db.con.execute(
        "SELECT matchday FROM matches WHERE competition_id = 'ENG1' AND season = ? AND "
        "matchday IS NOT NULL GROUP BY matchday HAVING bool_and(finished) AND matchday NOT IN "
        "(SELECT gw FROM fpl_points_gw WHERE season = ?) ORDER BY 1", [season, season]
    ).fetchall()]  # fmt: skip
    finished, detailed = db.con.execute(
        "SELECT count(*), count(*) FILTER (WHERE match_id IN (SELECT match_id FROM appearances "
        "WHERE source = 'fotmob')) FROM matches WHERE competition_id = 'ENG1' AND season = ? "
        "AND finished", [season]).fetchone()  # fmt: skip
    return {
        "fpl_snapshot": {"at": _iso(snap), "age_hours": None if age is None else round(age, 2),
                         "state": "missing" if snap is None else
                         "stale" if age > SNAPSHOT_STALE_AFTER.total_seconds() / 3600 else "ok"},
        "fpl_points": {"finished_gws_without_points": no_points,
                       "state": "missing_gws" if no_points else "ok"},
        "fotmob_detail": {"finished_matches": finished, "with_detail": detailed,
                          "state": "partial" if detailed < finished else "ok"},
    }  # fmt: skip


def report(db: FootballDB, season: int, at: datetime | None = None) -> dict[str, Any]:
    at = at or _now()
    srcs = sources(db, at)
    data = data_checks(db, season, at)
    blocked = [f"{s['source']} {s['state']}" for s in srcs
               if s["critical"] and s["state"] in ("failed", "stale")]  # fmt: skip
    if data["fpl_snapshot"]["state"] != "ok":
        blocked.append(f"FPL snapshot {data['fpl_snapshot']['state']}")
    degraded = [f"{s['source']} {s['state']}" for s in srcs
                if not s["critical"] and s["state"] in ("failed", "stale")]  # fmt: skip
    degraded += [f"{s['source']} never (no recorded attempt)" for s in srcs
                 if s["state"] == "never"]  # fmt: skip
    degraded += [f"{k} {v['state']}" for k, v in data.items()
                 if k != "fpl_snapshot" and v["state"] != "ok"]  # fmt: skip
    if "source_attempts" in getattr(db, "missing_tables", []):
        degraded.append("no source attempt record (database older than the schema): "
                        "failed or never-run sources cannot be seen")  # fmt: skip
    overall = "blocked" if blocked else "degraded" if degraded else "healthy"
    record = "source_attempts" not in getattr(db, "missing_tables", [])
    return {"at": _iso(at), "season": season, "overall": overall, "blocked": blocked,
            "degraded": degraded, "sources": srcs, "data": data,
            "attempt_record": record}  # fmt: skip
