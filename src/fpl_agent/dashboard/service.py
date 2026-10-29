"""What the dashboard shows, read from the agent's own database.

The database is opened read-only, for as long as it takes to build one consistent snapshot of
every section, and closed again so a refresh or plan can write. The snapshot is kept in memory
and rebuilt only when the database file changes and no local job is writing. Nothing here
fetches from the internet, runs a model or reads a secret: settings are reported as set / not
set, never by value. Queries are fixed; nothing the browser sends becomes SQL.
"""

from __future__ import annotations

import json
import math
import os
import re
import threading
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import duckdb

from fpl_agent.config import kill_switch_enabled
from fpl_agent.db.fpl_identity import reader_sources

STALE_SNAPSHOT_HOURS = 6.0  # an FPL snapshot older than this is shown as stale
LIMIT = 50  # rows per history list
STATUS = {
    "dry": "planned (dry run: nothing sent to FPL)",
    "live": "planned in live mode (execution outcome not recorded)",
    "live-done": "sent to FPL and verified by read-back",
}
OPTIONAL_KEYS = ("ANTHROPIC_API_KEY", "ODDS_API_KEY", "FPL_ENTRY_ID", "NOTIFY_WEBHOOK")


def _safe_reason(value: Any) -> str:
    text = str(value)
    for key in (
        "FPL_REFRESH_TOKEN",
        "ANTHROPIC_API_KEY",
        "ODDS_API_KEY",
        "GH_TOKEN",
        "GITHUB_TOKEN",
        "GH_PAT",
        "NOTIFY_WEBHOOK",
        "DISCORD_TOKEN",
    ):
        secret = os.environ.get(key)
        if secret:
            text = text.replace(secret, "[redacted]")
    text = re.sub(r"(?i)bearer\s+\S+|(?:sk-ant-|ghp_|github_pat_)\S+", "[redacted]", text)
    return text[:300]


def _iso(v: Any) -> str | None:
    if v is None:
        return None
    if isinstance(v, datetime):
        return v.replace(tzinfo=UTC).isoformat().replace("+00:00", "Z")
    return str(v)


def _num(v: Any, nd: int = 2) -> float | None:
    if v is None:
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(f) else round(f, nd)


def _hours_since(v: datetime | None, now: datetime) -> float | None:
    return None if v is None else round((now - v).total_seconds() / 3600, 1)


@dataclass
class Snapshot:
    state: str  # "ok" | "missing" | "empty" | "error"
    built_at: str
    version: tuple | None = None
    sections: dict[str, Any] = field(default_factory=dict)
    message: str = ""
    build_ms: float = 0.0
    locked: bool = False  # another program holds the database


class Reader:
    """One read-only pass over the database, producing every section."""

    def __init__(self, con: duckdb.DuckDBPyConnection, now: datetime) -> None:
        self.con, self.now = con, now
        self.tables = {
            r[0]
            for r in con.execute(
                "SELECT table_name FROM information_schema.tables WHERE table_schema = 'main'"
            ).fetchall()
        }

    def rows(self, sql: str, params: list | None = None, need: tuple[str, ...] = ()) -> list[dict]:
        if any(t not in self.tables for t in need):
            return []
        cur = self.con.execute(sql, params or [])
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, r, strict=True)) for r in cur.fetchall()]

    def one(self, sql: str, params: list | None = None, need: tuple[str, ...] = ()) -> dict:
        got = self.rows(sql, params, need)
        return got[0] if got else {}

    # ---- shared lookups ----------------------------------------------------------------
    def build(self) -> dict[str, Any]:
        cur = self.one(
            "SELECT season, max(gw) AS gw FROM fpl_players_gw "
            "WHERE season = (SELECT max(season) FROM fpl_players_gw) GROUP BY season",
            need=("fpl_players_gw",),
        )
        self.season, self.gw = cur.get("season"), cur.get("gw")
        self.names = {r["player_id"]: r["name"] for r in self.rows(
            "SELECT player_id, name FROM players", need=("players",))}  # fmt: skip
        self.teams = {r["team_id"]: r["name"] for r in self.rows(
            "SELECT team_id, name FROM teams", need=("teams",))}  # fmt: skip
        self.decisions = self._decisions()
        players = self._players()
        self.by_id = {p["player_id"]: p for p in players}
        return {
            "overview": self._overview(),
            "team": self._team(),
            "recommendations": self._recommendations(),
            "players": {"season": self.season, "gw": self.gw, "items": players},
            "planning": self._planning(),
            "activity": self._activity(),
            "health": self._health(),
        }

    def _decisions(self) -> list[dict]:
        out = []
        for r in self.rows(
            "SELECT season, gw, created_at, mode, payload FROM decisions "
            "ORDER BY created_at DESC LIMIT ?",
            [LIMIT],
            need=("decisions",),
        ):
            try:
                p = json.loads(r["payload"]) if r["payload"] else {}
            except ValueError:
                p = {}
            out.append({**r, "payload": p})
        return out

    def _projection_for(self, season: int, gw: int, before: datetime) -> dict[int, dict]:
        """The projection run the decision was made from: the newest one saved for that
        gameweek no later than the decision."""
        run = self.one(
            "SELECT run_id, created_at FROM projections WHERE season = ? AND gw = ? "
            "AND created_at <= ? ORDER BY created_at DESC LIMIT 1",
            [season, gw, before],
            need=("projections",),
        )
        if not run:
            return {}
        out = {}
        for r in self.rows(
            "SELECT player_id, xpts, detail FROM projections WHERE run_id = ? AND gw = ?",
            [run["run_id"], gw],
        ):
            try:
                det = json.loads(r["detail"]) if r["detail"] else {}
            except ValueError:
                det = {}
            out[r["player_id"]] = {
                "xp": _num(r["xpts"]), "q10": _num(det.get("q10")), "q90": _num(det.get("q90")),
                "p_zero": _num(det.get("p_zero")), "exp_minutes": _num(det.get("exp_minutes"), 0),
                "run_id": run["run_id"],
            }  # fmt: skip
        return out

    def _person(self, pid: int, proj: dict[int, dict]) -> dict:
        p = self.by_id.get(pid, {})
        return {
            "player_id": pid,
            "name": self.names.get(pid, f"player {pid}"),
            "position": p.get("position"),
            "team": p.get("team"),
            "price": p.get("price"),
            "status": p.get("status"),
            **{k: v for k, v in proj.get(pid, {}).items() if k != "run_id"},
        }

    def _decision_view(self, r: dict) -> dict:
        p = r["payload"]
        proj = self._projection_for(r["season"], r["gw"], r["created_at"])
        run_id = next(iter(proj.values()))["run_id"] if proj else None
        ids = lambda key: [int(x) for x in p.get(key) or []]  # noqa: E731
        return {
            "season": r["season"], "gw": r["gw"], "created_at": _iso(r["created_at"]),
            "mode": r["mode"], "status": STATUS.get(r["mode"], f"unknown mode {r['mode']!r}"),
            "entry": "unknown (decisions are not stored per FPL entry)",
            "projection_run": run_id,
            "chip": p.get("chip"), "hits": p.get("hits"), "xp": _num(p.get("xp")),
            "xp_no_transfers": _num(p.get("xp_no_transfers")),
            "bank_after": _num((p.get("bank_after") or 0) / 10, 1)
            if p.get("bank_after") is not None else None,
            "transfers_in": [self._person(i, proj) for i in ids("transfers_in")],
            "transfers_out": [self._person(i, proj) for i in ids("transfers_out")],
            "captain": self._person(int(p["captain"]), proj) if p.get("captain") else None,
            "vice": self._person(int(p["vice"]), proj) if p.get("vice") else None,
            "xi": [self._person(i, proj) for i in ids("xi")],
            "bench": [self._person(i, proj) for i in ids("bench")],
            "warnings": [str(w) for w in p.get("warnings") or []],
            "field_view": [_safe_reason(w) for w in p.get("field_view") or []],
            "checks": [_safe_reason(w) for w in p.get("checks") or []],
            "chip_timing": p.get("chip_timing") or {},
            "captain_options": [
                {"name": self.names.get(int(o["player_id"]), str(o["player_id"])),
                 "xp": _num(o.get("xpts"))}
                for o in p.get("captain_options") or [] if "player_id" in o
            ],
            "held_chips": p.get("held") or {},
            "next_moves": [
                {"gw": w.get("gw"),
                 "in": [self.names.get(int(i), str(i)) for i in w.get("in") or []],
                 "out": [self.names.get(int(i), str(i)) for i in w.get("out") or []]}
                for w in p.get("plan") or []
            ],
        }  # fmt: skip

    # ---- sections ----------------------------------------------------------------------
    def _snapshot_at(self) -> datetime | None:
        if self.season is None:
            return None
        return self.one(
            "SELECT max(snapshot_at) AS at FROM fpl_players_gw WHERE season = ? AND gw = ?",
            [self.season, self.gw],
        ).get("at")

    def _overview(self) -> dict:
        snap = self._snapshot_at()
        age = _hours_since(snap, self.now)
        kick = self.one(
            "SELECT min(m.kickoff) AS first FROM matches m WHERE m.match_id IN (SELECT match_id "
            "FROM match_ids WHERE source IN (?, ?)) AND m.season = ? AND m.matchday = ?",
            [*reader_sources(self.season), self.season, self.gw],
            need=("matches", "match_ids"),
        ).get("first") if self.season is not None else None  # fmt: skip
        last = self.one("SELECT max(run_at) AS at FROM ingest_log", need=("ingest_log",))
        latest = self.decisions[0] if self.decisions else None
        return {
            "season": self.season, "next_gw": self.gw,
            "snapshot_at": _iso(snap), "snapshot_age_hours": age,
            "snapshot_stale": age is None or age > STALE_SNAPSHOT_HOURS,
            "first_kickoff": _iso(kick),
            "deadline_note": "the exact FPL deadline is not stored locally; FPL usually sets "
                             "it about 90 minutes before the first kickoff",
            "last_ingest_at": _iso(last.get("at")),
            "latest_decision": {
                "season": latest["season"], "gw": latest["gw"],
                "created_at": _iso(latest["created_at"]), "mode": latest["mode"],
                "status": STATUS.get(latest["mode"], "unknown"),
                "xp": _num(latest["payload"].get("xp")),
            } if latest else None,
            "latest_run": next(iter(self._runs()), None),
            "counts": {
                "players": len(self.by_id), "decisions": self._count("decisions"),
                "projection_runs": self.one(
                    "SELECT count(DISTINCT run_id) AS n FROM projections",
                    need=("projections",)).get("n", 0),
                "reviews": self._count("reviews"), "lessons": self._count("lessons"),
            },
            "run_history": "recorded runs are shown in Agent History; older decisions may "
                           "have no linked execution record",
        }  # fmt: skip

    def _count(self, table: str) -> int:
        return self.one(f"SELECT count(*) AS n FROM {table}", need=(table,)).get("n", 0)

    def _team(self) -> dict:
        latest = next((d for d in self.decisions if d["gw"] == self.gw), None)
        latest = latest or (self.decisions[0] if self.decisions else None)
        base = {
            "public_squad": None,
            "public_squad_note": "the last deadline's public squad is read from FPL during a "
                                 "plan run and not stored; run a dry plan to refresh the "
                                 "planned squad below",
            "current_squad_note": "your current post-transfer squad needs an FPL sign-in, "
                                  "which the dashboard never does",
        }  # fmt: skip
        if not latest:
            return {**base, "planned": None}
        view = self._decision_view(latest)
        shape: dict[str, int] = {}
        for p in view["xi"]:
            shape[p["position"] or "?"] = shape.get(p["position"] or "?", 0) + 1
        formation = "-".join(str(shape.get(k, 0)) for k in ("DEF", "MID", "FWD"))
        return {**base, "planned": {**view, "formation": formation}}

    def _recommendations(self) -> dict:
        return {"items": [self._decision_view(d) for d in self.decisions[:20]]}

    def _players(self) -> list[dict]:
        if self.season is None:
            return []
        history: dict[int, list[dict]] = {}
        for row in self.rows(
            "SELECT player_id, gw, sum(total_points) AS points, sum(minutes) AS minutes "
            "FROM fpl_points_gw WHERE season = ? AND fixture_id <> 0 "
            "AND player_id IS NOT NULL GROUP BY player_id, gw ORDER BY gw DESC",
            [self.season],
            need=("fpl_points_gw",),
        ):
            history.setdefault(row["player_id"], []).append(
                {k: row[k] for k in ("gw", "points", "minutes")}
            )
        pts = {r["player_id"]: r for r in self.rows(
            "SELECT player_id, sum(total_points) AS points, sum(minutes) AS minutes, "
            "count(*) FILTER (WHERE fixture_id <> 0) AS fixtures FROM fpl_points_gw "
            "WHERE season = ? AND player_id IS NOT NULL GROUP BY player_id",
            [self.season], need=("fpl_points_gw",))}  # fmt: skip
        run = self.one(
            "SELECT run_id FROM projections WHERE season = ? AND gw = ? "
            "ORDER BY created_at DESC LIMIT 1",
            [self.season, self.gw],
            need=("projections",),
        ).get("run_id")
        proj = {r["player_id"]: r for r in self.rows(
            "SELECT player_id, xpts, detail FROM projections WHERE run_id = ? AND gw = ?",
            [run, self.gw])} if run else {}  # fmt: skip
        out = []
        for r in self.rows(
            "SELECT player_id, fpl_id, team_id, position, now_cost, status, chance_next, news, "
            "selected_by_percent, raw FROM fpl_players_gw WHERE season = ? AND gw = ? "
            "QUALIFY snapshot_at = max(snapshot_at) OVER (PARTITION BY fpl_id)",
            [self.season, self.gw],
        ):
            pid = r["player_id"]
            pr = proj.get(pid)
            try:
                det = json.loads(pr["detail"]) if pr and pr["detail"] else {}
            except ValueError:
                det = {}
            h = pts.get(pid, {})
            try:
                raw = json.loads(r["raw"]) if r["raw"] else {}
            except ValueError:
                raw = {}
            out.append({
                "player_id": pid, "fpl_id": r["fpl_id"], "name": self.names.get(pid, str(pid)),
                "team": self.teams.get(r["team_id"]), "position": r["position"],
                "price": _num((r["now_cost"] or 0) / 10, 1), "status": r["status"],
                "chance": r["chance_next"], "news": r["news"] or "",
                "owned": _num(r["selected_by_percent"], 1),
                "points": h.get("points"), "minutes": h.get("minutes"),
                "fixtures_played_rows": h.get("fixtures"),
                "xp": _num(pr["xpts"]) if pr else None,
                "q10": _num(det.get("q10")), "q90": _num(det.get("q90")),
                "exp_minutes": _num(det.get("exp_minutes"), 0),
                "form": _num(raw.get("form")),
                "history": history.get(pid, [])[:6],
            })  # fmt: skip
        out.sort(key=lambda p: (p["xp"] is None, -(p["xp"] or 0), p["name"]))
        return out

    def _planning(self) -> dict:
        if self.season is None:
            return {"weeks": [], "unscheduled": [], "projection": []}
        fx = self.rows(
            "SELECT m.matchday AS gw, m.kickoff, m.home_team_id, m.away_team_id, m.finished, "
            "m.home_goals, m.away_goals FROM matches m WHERE m.match_id IN (SELECT match_id "
            "FROM match_ids WHERE source IN (?, ?)) AND m.season = ? AND (m.matchday IS NULL OR "
            "m.matchday BETWEEN ? AND ?) ORDER BY m.matchday NULLS LAST, m.kickoff, m.match_id",
            [*reader_sources(self.season), self.season, self.gw, self.gw + 7],
            need=("matches", "match_ids"),
        )
        weeks: dict[int, list] = {}
        unscheduled = []
        for f in fx:
            row = {"home": self.teams.get(f["home_team_id"]),
                   "away": self.teams.get(f["away_team_id"]),
                   "kickoff": _iso(f["kickoff"]), "finished": bool(f["finished"]),
                   "score": None if f["home_goals"] is None
                   else f"{f['home_goals']}-{f['away_goals']}"}  # fmt: skip
            if f["gw"] is None:
                unscheduled.append(row)
            else:
                weeks.setdefault(f["gw"], []).append(row)
        latest = self.decisions[0] if self.decisions else None
        xi = [int(x) for x in (latest["payload"].get("xi") or [])] if latest else []
        run = self.one(
            "SELECT run_id FROM projections WHERE season = ? ORDER BY created_at DESC LIMIT 1",
            [self.season], need=("projections",)).get("run_id")  # fmt: skip
        proj = self.rows(
            "SELECT gw, count(*) AS players, sum(xpts) FILTER (WHERE list_contains(?, player_id)) "
            "AS xi_xp FROM projections WHERE run_id = ? GROUP BY gw ORDER BY gw",
            [xi, run],
        ) if run else []  # fmt: skip
        return {
            "weeks": [{"gw": gw, "fixtures": weeks[gw]} for gw in sorted(weeks)],
            "unscheduled": unscheduled,
            "projection": [{"gw": r["gw"], "players": r["players"],
                            "xi_xp": _num(r["xi_xp"]) if xi else None} for r in proj],  # fmt: skip
            "projection_note": "xi_xp is the latest plan's XI kept unchanged across the "
                               "horizon (no further transfers or captain); the plan itself "
                               "may change it",
            "decision": self._decision_view(latest) if latest else None,
        }  # fmt: skip

    def _activity(self) -> dict:
        reviews = []
        for r in self.rows(
            "SELECT season, gw, kind, created_at, payload FROM reviews "
            "ORDER BY created_at DESC LIMIT ?",
            [LIMIT],
            need=("reviews",),
        ):
            try:
                p = json.loads(r["payload"]) if r["payload"] else {}
            except ValueError:
                p = {}
            summary = {k: v for k, v in p.items() if isinstance(v, int | float | str)}
            reviews.append({**{k: r[k] for k in ("season", "gw", "kind")},
                            "created_at": _iso(r["created_at"]),
                            "summary": dict(list(summary.items())[:8])})  # fmt: skip
        return {
            "runs": self._runs(),
            "scout_calls": [
                {**r, "called_at": _iso(r["called_at"])} for r in self.rows(
                    "SELECT called_at, model, gw, eligible, cached, sent, accepted, rejected, "
                    "input_bytes, input_tokens, output_tokens, latency_ms, outcome "
                    "FROM scout_calls "
                    "ORDER BY called_at DESC LIMIT ?", [LIMIT], need=("scout_calls",),
                )
            ],
            "decisions": [
                {"season": d["season"], "gw": d["gw"], "created_at": _iso(d["created_at"]),
                 "mode": d["mode"], "status": STATUS.get(d["mode"], "unknown")}
                for d in self.decisions
            ],
            "reviews": reviews,
            "lessons": [
                {**r, "created_at": _iso(r["created_at"]), "points": _num(r["points"])}
                for r in self.rows(
                    "SELECT season, gw, kind, points, what, lesson, created_at FROM lessons "
                    "ORDER BY created_at DESC LIMIT ?", [LIMIT], need=("lessons",))
            ],
            "ingest": [
                {**r, "run_at": _iso(r["run_at"])}
                for r in self.rows(
                    "SELECT source, run_at, table_name, rows, note FROM ingest_log "
                    "ORDER BY run_at DESC LIMIT ?", [LIMIT * 2], need=("ingest_log",))
            ],
        }  # fmt: skip

    def _runs(self) -> list[dict]:
        out = []
        for row in self.rows(
            "SELECT run_id, season, gw, entry_id, mode, started_at, ended_at, "
            "status, state, exit_code, delivered, reasons, stages FROM runs "
            "ORDER BY started_at DESC LIMIT ?",
            [LIMIT],
            need=("runs",),
        ):
            for key in ("reasons", "stages"):
                try:
                    row[key] = json.loads(row[key]) if row[key] else None
                except (ValueError, TypeError):
                    row[key] = None
            reasons = row["reasons"]
            row["reasons"] = (
                [_safe_reason(v) for v in reasons[:20]] if isinstance(reasons, list) else []
            )
            stages = row["stages"]
            row["stage_seconds"] = (
                {
                    _safe_reason(k): _num(v.get("seconds"))
                    for k, v in stages.items()
                    if isinstance(v, dict)
                }
                if isinstance(stages, dict)
                else {}
            )
            row.pop("stages")
            out.append(
                {**row, "started_at": _iso(row["started_at"]), "ended_at": _iso(row["ended_at"])}
            )
        return out

    def _health(self) -> dict:
        from fpl_agent.news import BBC, CITY, freshness, timestamp

        news = []
        for row in self.rows(
            "SELECT observed_at, payload FROM news_evidence "
            "QUALIFY row_number() OVER (PARTITION BY url ORDER BY observed_at DESC) = 1 "
            "ORDER BY observed_at DESC, url DESC LIMIT 100",
            need=("news_evidence",),
        ):
            try:
                item = json.loads(row["payload"])
                if not (item.get("players") or item.get("error") or item.get("url") in (CITY, BBC)):
                    continue
                state = (
                    "failed"
                    if item.get("error")
                    else freshness(
                        timestamp(item.get("published_at")), self.now.replace(tzinfo=UTC)
                    )
                )
                news.append(
                    {
                        "url": _safe_reason(item.get("url", "")),
                        "title": _safe_reason(item.get("title", "")),
                        "source": _safe_reason(item.get("source", "")),
                        "state": state,
                        "observed_at": _iso(row["observed_at"]),
                        "published_at": item.get("published_at"),
                        "error": _safe_reason(item.get("error") or ""),
                    }
                )
            except (ValueError, TypeError, AttributeError):
                continue
        snap = self._snapshot_at()
        age = _hours_since(snap, self.now)
        sources = [
            {**r, "last_success_at": _iso(r["last_success_at"]),
             "age_hours": _hours_since(r["last_success_at"], self.now)}
            for r in self.rows(
                "SELECT source, table_name, max(run_at) AS last_success_at, "
                "arg_max(rows, run_at) AS rows, arg_max(note, run_at) AS note "
                "FROM ingest_log GROUP BY source, table_name ORDER BY source, table_name",
                need=("ingest_log",))
        ]  # fmt: skip
        state = self.rows(
            "SELECT source, status, count(*) AS n, max(updated_at) AS last FROM ingest_state "
            "GROUP BY source, status ORDER BY source, status",
            need=("ingest_state",),
        )
        points_gw = self.one(
            "SELECT max(gw) AS gw FROM fpl_points_gw WHERE season = ?", [self.season],
            need=("fpl_points_gw",)).get("gw") if self.season is not None else None  # fmt: skip
        tables = sorted(self.tables)
        return {
            "snapshot_at": _iso(snap), "snapshot_age_hours": age,
            "snapshot_stale": age is None or age > STALE_SNAPSHOT_HOURS,
            "stale_after_hours": STALE_SNAPSHOT_HOURS,
            "points_history_through_gw": points_gw,
            "sources": sources,
            "news": news[:20],
            "source_note": "the database records successful loads only; failed or skipped "
                           "sources appear in the db-update output, not here",
            "ingest_state": [{**r, "last": _iso(r["last"])} for r in state],
            "tables": [{"table": t, "rows": self._count(t)} for t in tables],
            "settings": settings_presence(),
            "api_note": "the dashboard never calls FPL or any other API; source availability "
                        "is only known from the last refresh",
        }  # fmt: skip


def settings_presence() -> dict[str, Any]:
    """Which settings exist, never their values."""
    mode = os.environ.get("AGENT_MODE", "dry").strip().lower()
    return {
        "agent_mode": mode if mode in ("dry", "live") else "dry",
        "agent_enabled": kill_switch_enabled(os.environ.get("AGENT_ENABLED")),
        "keys": {k: bool(os.environ.get(k)) for k in OPTIONAL_KEYS},
        "dashboard_jobs": "always dry: no sign-in, no notifications",
    }


class ReadModel:
    """The cached snapshot. `busy()` says a local job is writing; the last good snapshot is
    served (and labelled) until it finishes."""

    def __init__(self, db_path: Path, busy=lambda: False) -> None:
        self.path = Path(db_path)
        self.busy = busy
        self._lock = threading.Lock()
        self._snap: Snapshot | None = None

    def version(self) -> tuple | None:
        parts = []
        for p in (self.path, self.path.with_name(self.path.name + ".wal")):
            try:
                st = p.stat()
                parts.append((st.st_mtime_ns, st.st_size))
            except FileNotFoundError:
                parts.append(None)
        return None if parts[0] is None else tuple(parts)

    def get(self) -> tuple[Snapshot, dict[str, Any]]:
        """(snapshot, flags) where flags say whether it is stale because of a running job or a
        database held by another process."""
        with self._lock:
            ver = self.version()
            flags = {"refresh_in_progress": bool(self.busy()), "db_locked": False}
            if self._snap is not None and self._snap.version == ver:
                return self._snap, flags
            if flags["refresh_in_progress"] and self._snap is not None:
                return self._snap, flags
            snap = self._build(ver)
            if snap.locked:
                flags["db_locked"] = True
                if self._snap is not None:
                    return self._snap, flags
            self._snap = snap
            return snap, flags

    def _build(self, ver: tuple | None) -> Snapshot:
        now = datetime.now(UTC).replace(tzinfo=None)
        t0 = time.perf_counter()
        if ver is None:
            return Snapshot("missing", _iso(now), None, {}, "no database yet: run a refresh")
        try:
            con = duckdb.connect(str(self.path), read_only=True)
        except duckdb.Error as exc:
            locked = _is_locked(exc)
            return Snapshot("error", _iso(now), None if locked else ver, {}, _short(exc),
                            locked=locked)  # fmt: skip
        try:
            sections = Reader(con, now).build()
        except duckdb.Error as exc:
            return Snapshot("error", _iso(now), ver, {}, _short(exc))
        finally:
            con.close()
        state = "ok" if sections["overview"]["season"] is not None else "empty"
        msg = "" if state == "ok" else "the database has no FPL snapshot yet: run a refresh"
        ms = round((time.perf_counter() - t0) * 1000, 1)
        return Snapshot(state, _iso(now), ver, sections, msg, ms)


def _is_locked(exc: Exception) -> bool:
    text = str(exc).lower()
    return isinstance(exc, duckdb.IOException) and (
        "lock" in text or "used by another process" in text
    )


def _short(exc: Exception) -> str:
    """A database error for the page, without file paths."""
    if _is_locked(exc):
        return "the database is in use by another program (a CLI run?); try again when it ends"
    return f"{type(exc).__name__}: database could not be read"
