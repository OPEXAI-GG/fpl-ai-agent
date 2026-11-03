"""Point in time: deadlines, data cutoffs, forecast runs and whether a gameweek's results are final.

Every time here is naive UTC (the database's convention, see `db.database.now`).

* **Deadline.** FPL's own `deadline_time` per gameweek, recorded from the bootstrap in
  `fpl_events`. When it was never recorded (older files) it is estimated as 90 minutes before
  the gameweek's first kickoff and labelled `estimated`.
* **Cutoff.** The data a forecast may use: rows observed at or before it. For a live run it is
  the moment the run starts; for a backtest it is the gameweek's deadline.
* **Forecast run.** One stored projection run (`forecast_runs`): when it was made, its cutoff, its
  first gameweek's deadline and the model/feature versions behind it. It is *eligible* for review
  only when it was made before that deadline. A review scores exactly one eligible run, never a
  mix of runs per player.
* **Final.** A gameweek's results are final when FPL marks it `finished` and `data_checked`.
  Provisional results may be shown, but never feed calibration, lessons or the learning state.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import pandas as pd

from fpl_agent.db.database import FootballDB, now

DEADLINE_LEAD = timedelta(minutes=90)  # FPL's deadline is 90 minutes before the first kickoff
MATCH_FACT_DELAY = timedelta(hours=3)  # a match's lineups/minutes are public ~3h after kickoff

MODEL_VERSION = "sim-v1.1"
FEATURE_VERSION = "f2026.11"


def utc(ts: Any) -> datetime | None:
    """Any timestamp (ISO string, aware or naive datetime, pandas Timestamp) as naive UTC."""
    if ts is None or (not isinstance(ts, str | datetime) and pd.isna(ts)):
        return None
    if isinstance(ts, str):
        ts = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    if isinstance(ts, pd.Timestamp):
        ts = ts.to_pydatetime()
    if ts.tzinfo is not None:
        ts = ts.astimezone(UTC).replace(tzinfo=None)
    return ts


# ---- FPL events --------------------------------------------------------------------------------
def record_events(db: FootballDB, raw: dict[str, Any], season: int) -> int:
    """Store each gameweek's deadline and its finished/data_checked flags from the bootstrap."""
    rows = [
        {"season": season, "gw": int(e["id"]), "deadline_time": utc(e.get("deadline_time")),
         "finished": bool(e.get("finished")), "data_checked": bool(e.get("data_checked")),
         "observed_at": now()}
        for e in raw.get("events", []) if e.get("id") is not None
    ]  # fmt: skip
    return db.upsert("fpl_events", pd.DataFrame(rows)) if rows else 0


@dataclass(frozen=True)
class Deadline:
    at: datetime | None
    source: str  # "fpl" | "estimated" | "unknown"


def deadline(db: FootballDB, season: int, gw: int) -> Deadline:
    row = db.con.execute(
        "SELECT deadline_time FROM fpl_events WHERE season = ? AND gw = ?", [season, gw]
    ).fetchone()
    if row and row[0] is not None:
        return Deadline(utc(row[0]), "fpl")
    row = db.con.execute(
        "SELECT min(kickoff) FROM matches WHERE competition_id = 'ENG1' AND season = ? "
        "AND matchday = ?",
        [season, gw],
    ).fetchone()
    if row and row[0] is not None:
        return Deadline(utc(row[0]) - DEADLINE_LEAD, "estimated")
    return Deadline(None, "unknown")


@dataclass(frozen=True)
class Finality:
    final: bool
    reason: str


def finality(db: FootballDB, season: int, gw: int) -> Finality:
    """Are this gameweek's official results complete and checked by FPL?"""
    row = db.con.execute(
        "SELECT finished, data_checked FROM fpl_events WHERE season = ? AND gw = ?", [season, gw]
    ).fetchone()
    if row is None:
        return Finality(False, "no FPL event status recorded (run db-update)")
    if not row[0]:
        return Finality(False, "gameweek not finished")
    if not row[1]:
        return Finality(False, "gameweek finished but FPL has not checked the data (bonus may move)")
    n = db.con.execute(
        "SELECT count(*) FROM fpl_points_gw WHERE season = ? AND gw = ?", [season, gw]
    ).fetchone()[0]
    if not n:
        return Finality(False, "no official points loaded for the gameweek")
    return Finality(True, "finished and data checked")


# ---- forecast runs -----------------------------------------------------------------------------
def register_run(
    db: FootballDB,
    run_id: str,
    season: int,
    gws: list[int],
    created_at: datetime,
    cutoff: datetime | None,
    kind: str = "live",
    variant: str = "primary",
    parent_run_id: str | None = None,
    model: str = "sim-v1",
    flags: dict[str, Any] | None = None,
    limitations: list[str] | None = None,
) -> dict[str, Any]:
    first = int(gws[0])
    dl = deadline(db, season, first)
    row = {
        "run_id": run_id, "season": season, "first_gw": first,
        "gws": ",".join(str(g) for g in gws), "created_at": created_at, "cutoff": cutoff,
        "deadline": dl.at, "deadline_source": dl.source,
        "eligible": bool(dl.at is not None and created_at < dl.at
                         and (cutoff is None or cutoff <= dl.at)),
        "kind": kind, "variant": variant, "parent_run_id": parent_run_id, "model": model,
        "model_version": MODEL_VERSION, "feature_version": FEATURE_VERSION,
        "flags": json.dumps(flags or {}, default=str),
        "limitations": json.dumps(limitations or []),
    }  # fmt: skip
    db.upsert("forecast_runs", pd.DataFrame([row]))
    return row


@dataclass(frozen=True)
class RunChoice:
    run_id: str | None
    created_at: datetime | None
    deadline: datetime | None
    deadline_source: str
    legacy: bool  # chosen from projections without a forecast_runs row
    reason: str
    flags: dict[str, Any] | None = None

    @property
    def found(self) -> bool:
        return self.run_id is not None


def eligible_run(
    db: FootballDB, season: int, gw: int, variant: str = "primary", kind: str = "live"
) -> RunChoice:
    """The newest run that forecast `gw` and was made before its deadline (one run, whole).

    Runs registered in `forecast_runs` are preferred. Projections stored before runs were
    registered are grouped by run id and judged against the deadline the same way.
    """
    dl = deadline(db, season, gw)
    if dl.at is None:
        return RunChoice(None, None, None, dl.source, False, "no deadline known for the gameweek")
    row = db.con.execute(
        "SELECT r.run_id, r.created_at, r.flags FROM forecast_runs r "
        "WHERE r.season = ? AND r.variant = ? AND r.kind = ? AND r.created_at < ? "
        "AND (r.cutoff IS NULL OR r.cutoff <= ?) "
        "AND EXISTS (SELECT 1 FROM projections p WHERE p.run_id = r.run_id AND p.gw = ?) "
        "ORDER BY r.created_at DESC LIMIT 1",
        [season, variant, kind, dl.at, dl.at, gw],
    ).fetchone()
    if row:
        return RunChoice(row[0], utc(row[1]), dl.at, dl.source, False, "registered run",
                         json.loads(row[2]) if row[2] else {})  # fmt: skip
    if variant != "primary":
        return RunChoice(None, None, dl.at, dl.source, False, f"no eligible {variant} run")
    # legacy projections (no run registry): whole runs created before the deadline
    row = db.con.execute(
        "SELECT run_id, max(created_at) AS made FROM projections p WHERE season = ? AND gw = ? "
        "AND created_at < ? AND NOT EXISTS (SELECT 1 FROM forecast_runs r "
        "WHERE r.run_id = p.run_id) GROUP BY run_id ORDER BY made DESC LIMIT 1",
        [season, gw, dl.at],
    ).fetchone()
    if row:
        return RunChoice(row[0], utc(row[1]), dl.at, dl.source, True, "legacy run before deadline")
    return RunChoice(None, None, dl.at, dl.source, False, "no forecast made before the deadline")


def run_projection(db: FootballDB, run_id: str | None, season: int, gw: int) -> pd.DataFrame:
    """One run's rows for one gameweek (a legacy run without an id: none)."""
    if run_id is None:
        return pd.DataFrame(columns=["player_id", "xpts", "detail"])
    return db.query(
        "SELECT player_id, xpts, detail FROM projections WHERE run_id = ? AND season = ? AND gw = ?",
        [run_id, season, gw],
    ).drop_duplicates("player_id")
