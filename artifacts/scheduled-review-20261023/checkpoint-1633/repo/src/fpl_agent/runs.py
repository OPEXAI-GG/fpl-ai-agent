"""Run records: one row per weekly run, whatever way it ended, so a skipped, held, blocked or
failed run is as visible as a successful one, and a report is re-sent until it was delivered.

States are kept apart: `planned` (a decision exists), `applied` (FPL accepted moves), `verified`
(FPL's team was read back and matched), and `delivered` (every chunk of the report was posted).
Rows are keyed by entry; older decisions without a run row stay "entry/outcome unknown".
"""

from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import pandas as pd

from fpl_agent.db.database import FootballDB, now

VERSION = 1
ALARM_HOURS = 3.0  # default: no good run this close to the deadline raises the alarm


@dataclass
class RunRecord:
    season: int
    entry_id: int | None
    mode: str
    run_id: str = field(default_factory=lambda: uuid.uuid4().hex[:16])
    started_at: datetime = field(default_factory=now)
    stages: dict[str, dict[str, Any]] = field(default_factory=dict)
    _t: dict[str, datetime] = field(default_factory=dict, repr=False)

    def start(self, stage: str) -> None:
        self._t[stage] = now()

    def end(self, stage: str, status: str = "ok") -> None:
        t0 = self._t.pop(stage, None)
        secs = (now() - t0).total_seconds() if t0 else None
        self.stages[stage] = {"status": status, "seconds": round(secs, 3) if secs else secs}


def state_of(decision, outcome) -> str:
    if outcome is not None and outcome.mode == "live":
        if outcome.verified is True:
            return "verified"
        if outcome.done and not outcome.errors:
            return "applied"
    return "planned" if decision is not None else "none"


def alert_key(reasons: list[str]) -> str:
    return hashlib.sha256("\n".join(sorted(reasons)).encode()).hexdigest()[:16]


def save(db: FootballDB, rec: RunRecord, *, gw: int | None, status: str, state: str,
         exit_code: int, reasons: list[str], snapshot_at: datetime | None,
         projection_run: str | None, decision_at: datetime | None, report_key: str | None,
         delivered: bool | None) -> None:  # fmt: skip
    db.upsert("runs", pd.DataFrame([{
        "run_id": rec.run_id, "version": VERSION, "season": rec.season, "gw": gw,
        "entry_id": rec.entry_id, "mode": rec.mode, "started_at": rec.started_at,
        "ended_at": now(), "status": status, "state": state, "exit_code": exit_code,
        "snapshot_at": snapshot_at, "projection_run": projection_run,
        "decision_at": decision_at, "stages": json.dumps(rec.stages),
        "reasons": json.dumps(reasons[:20]), "report_key": report_key,
        "delivered": delivered, "delivered_at": now() if delivered else None,
    }]))  # fmt: skip


def delivered_before(db: FootballDB, season: int, gw: int, entry: int | None, key: str) -> bool:
    row = db.con.execute(
        "SELECT 1 FROM runs WHERE season = ? AND gw = ? AND entry_id IS NOT DISTINCT FROM ? "
        "AND report_key = ? AND delivered LIMIT 1",
        [season, gw, entry, key],
    ).fetchone()
    return row is not None


@dataclass
class Coverage:
    alarm: bool
    message: str


def coverage(db: FootballDB, season: int, gw: int, entry: int | None, deadline: datetime,
             live: bool, at: datetime | None = None,
             alarm_hours: float = ALARM_HOURS) -> Coverage:  # fmt: skip
    """Alarm when the deadline is within `alarm_hours` (and not passed) and no run for this
    gameweek reached a good state: verified moves in live mode, a plan in dry mode."""
    at = at or datetime.now(UTC)
    hours = (deadline - at).total_seconds() / 3600
    if hours < 0 or hours > alarm_hours:
        return Coverage(False, f"GW{gw} deadline {hours:.1f}h away: outside the alarm window")
    good = ("verified",) if live else ("planned", "applied", "verified")
    marks = ", ".join("?" for _ in good)
    row = db.con.execute(
        f"SELECT max(ended_at) FROM runs WHERE season = ? AND gw = ? AND "
        f"entry_id IS NOT DISTINCT FROM ? AND status IN ('ok', 'held') AND state IN ({marks})",
        [season, gw, entry, *good],
    ).fetchone()
    if row and row[0] is not None:
        return Coverage(False, f"GW{gw}: a {'verified' if live else 'planned'} run finished at "
                               f"{row[0]:%Y-%m-%d %H:%M} UTC")  # fmt: skip
    last = db.con.execute(
        "SELECT status, state, ended_at FROM runs WHERE season = ? AND gw = ? "
        "AND entry_id IS NOT DISTINCT FROM ? ORDER BY ended_at DESC LIMIT 1",
        [season, gw, entry],
    ).fetchone()
    seen = (f"last run: {last[0]} / {last[1]} at {last[2]:%H:%M} UTC" if last
            else "no run recorded for this gameweek")  # fmt: skip
    need = "verified moves" if live else "a plan"
    return Coverage(True, f"FPL agent alarm: the GW{gw} deadline is {hours:.1f}h away and no "
                          f"run has produced {need} ({seen}). Check the plan workflow or run it "
                          "by hand.")  # fmt: skip
