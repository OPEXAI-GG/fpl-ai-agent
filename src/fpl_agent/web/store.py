"""SQLite application state for the website: sessions, jobs, results, provider cache and
rate limits. Separate from the football database, which the service only ever reads.

* Shared state (provider responses for bootstrap/fixtures, shared forecasts) carries no
  visitor; per-visitor state (session, preferences) and per-entry results are keyed by
  session and entry, so one visitor's preferences or jobs never change another's.
* Migrations are numbered and applied in order (`PRAGMA user_version`), so an existing file
  upgrades in place.
* Every write is a short transaction; WAL lets the API read while the worker writes.
"""

from __future__ import annotations

import json
import secrets
import sqlite3
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

MIGRATIONS: list[str] = [
    # 1: initial schema
    """
    CREATE TABLE sessions (
        id TEXT PRIMARY KEY,
        created_at REAL NOT NULL,
        last_seen REAL NOT NULL,
        entry_id INTEGER,
        prefs TEXT NOT NULL DEFAULT '{}'
    );
    CREATE TABLE jobs (
        id TEXT PRIMARY KEY,
        kind TEXT NOT NULL,              -- forecast | plan
        dedup_key TEXT NOT NULL,         -- identical work shares one job
        status TEXT NOT NULL,            -- queued | running | done | failed | timed_out
        stage TEXT NOT NULL DEFAULT '',
        progress REAL NOT NULL DEFAULT 0,
        params TEXT NOT NULL,
        result TEXT,
        error TEXT,
        session_id TEXT,
        entry_id INTEGER,
        created_at REAL NOT NULL,
        started_at REAL,
        finished_at REAL,
        heartbeat REAL,
        attempts INTEGER NOT NULL DEFAULT 0,
        worker TEXT
    );
    CREATE INDEX jobs_status ON jobs(status, created_at);
    CREATE INDEX jobs_dedup ON jobs(dedup_key, created_at);
    CREATE TABLE results (
        key TEXT PRIMARY KEY,            -- plan cache key (forecast + squad state + prefs)
        kind TEXT NOT NULL,
        entry_id INTEGER,
        stage TEXT NOT NULL,             -- preliminary | final
        created_at REAL NOT NULL,
        job_id TEXT,
        body TEXT NOT NULL
    );
    CREATE INDEX results_entry ON results(entry_id, created_at);
    CREATE TABLE provider_cache (
        key TEXT PRIMARY KEY,
        fetched_at REAL NOT NULL,
        body TEXT NOT NULL
    );
    CREATE TABLE rate_limits (
        bucket TEXT NOT NULL,
        window INTEGER NOT NULL,
        count INTEGER NOT NULL,
        PRIMARY KEY (bucket, window)
    );
    CREATE TABLE forecasts (
        key TEXT PRIMARY KEY,
        revision TEXT NOT NULL,
        created_at REAL NOT NULL,
        path TEXT NOT NULL,
        manifest TEXT NOT NULL
    );
    CREATE TABLE revisions (
        id TEXT PRIMARY KEY,
        path TEXT NOT NULL,
        created_at REAL NOT NULL,
        meta TEXT NOT NULL,
        current INTEGER NOT NULL DEFAULT 0
    );
    """,
]

ACTIVE = ("queued", "running")


def _now() -> float:
    return time.time()


@dataclass
class Job:
    id: str
    kind: str
    dedup_key: str
    status: str
    stage: str
    progress: float
    params: dict
    result: dict | None
    error: str | None
    session_id: str | None
    entry_id: int | None
    created_at: float
    started_at: float | None
    finished_at: float | None
    heartbeat: float | None
    attempts: int

    @classmethod
    def from_row(cls, r: sqlite3.Row) -> Job:
        return cls(
            r["id"],
            r["kind"],
            r["dedup_key"],
            r["status"],
            r["stage"],
            float(r["progress"]),
            json.loads(r["params"]),
            json.loads(r["result"]) if r["result"] else None,
            r["error"],
            r["session_id"],
            r["entry_id"],
            r["created_at"],
            r["started_at"],
            r["finished_at"],
            r["heartbeat"],
            r["attempts"],
        )

    def public(self) -> dict[str, Any]:
        """What a visitor may see about a job (never another visitor's session id)."""
        return {
            "id": self.id,
            "kind": self.kind,
            "status": self.status,
            "stage": self.stage,
            "progress": round(self.progress, 3),
            "error": self.error,
            "created_at": self.created_at,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "result": self.result,
        }


class Store:
    def __init__(self, path: str | Path) -> None:
        self.path = str(path)
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._local = threading.local()
        self.migrate()

    # ---- connection ------------------------------------------------------------------------
    def con(self) -> sqlite3.Connection:
        c = getattr(self._local, "con", None)
        if c is None:
            c = sqlite3.connect(
                self.path, timeout=30, isolation_level=None, check_same_thread=False
            )
            c.row_factory = sqlite3.Row
            c.execute("PRAGMA journal_mode=WAL")
            c.execute("PRAGMA busy_timeout=30000")
            c.execute("PRAGMA foreign_keys=ON")
            self._local.con = c
        return c

    @contextmanager
    def tx(self) -> Iterator[sqlite3.Connection]:
        c = self.con()
        c.execute("BEGIN IMMEDIATE")
        try:
            yield c
        except BaseException:
            c.execute("ROLLBACK")
            raise
        c.execute("COMMIT")

    def migrate(self) -> int:
        c = self.con()
        have = c.execute("PRAGMA user_version").fetchone()[0]
        for i, sql in enumerate(MIGRATIONS[have:], start=have + 1):
            with self.tx() as t:
                for stmt in (s.strip() for s in sql.split(";")):
                    if stmt:
                        t.execute(stmt)
                t.execute(f"PRAGMA user_version = {i}")
        return c.execute("PRAGMA user_version").fetchone()[0]

    # ---- sessions --------------------------------------------------------------------------
    def new_session(self) -> str:
        sid = secrets.token_urlsafe(24)
        t = _now()
        with self.tx() as c:
            c.execute(
                "INSERT INTO sessions (id, created_at, last_seen) VALUES (?, ?, ?)", [sid, t, t]
            )
        return sid

    def session(self, sid: str | None) -> dict | None:
        if not sid:
            return None
        r = self.con().execute("SELECT * FROM sessions WHERE id = ?", [sid]).fetchone()
        if r is None:
            return None
        self.con().execute("UPDATE sessions SET last_seen = ? WHERE id = ?", [_now(), sid])
        return {
            "id": r["id"],
            "entry_id": r["entry_id"],
            "prefs": json.loads(r["prefs"]),
            "created_at": r["created_at"],
        }

    def update_session(
        self, sid: str, *, entry_id: int | None | str = "keep", prefs: dict | None = None
    ) -> dict | None:
        with self.tx() as c:
            r = c.execute("SELECT prefs FROM sessions WHERE id = ?", [sid]).fetchone()
            if r is None:
                return None
            if entry_id != "keep":
                c.execute("UPDATE sessions SET entry_id = ? WHERE id = ?", [entry_id, sid])
            if prefs is not None:
                merged = {**json.loads(r["prefs"]), **prefs}
                c.execute("UPDATE sessions SET prefs = ? WHERE id = ?", [json.dumps(merged), sid])
        return self.session(sid)

    def delete_session(self, sid: str) -> None:
        with self.tx() as c:
            c.execute("DELETE FROM sessions WHERE id = ?", [sid])

    # ---- jobs ------------------------------------------------------------------------------
    def enqueue(
        self,
        kind: str,
        dedup_key: str,
        params: dict,
        *,
        session_id: str | None = None,
        entry_id: int | None = None,
    ) -> tuple[Job, bool]:
        """(job, created). An active job with the same key is returned instead of a new one,
        so a visitor reloading the page, or two visitors asking for the same work, share it."""
        with self.tx() as c:
            r = c.execute(
                "SELECT * FROM jobs WHERE dedup_key = ? AND status IN ('queued', 'running') "
                "ORDER BY created_at LIMIT 1",
                [dedup_key],
            ).fetchone()
            if r is not None:
                return Job.from_row(r), False
            jid = secrets.token_hex(8)
            c.execute(
                "INSERT INTO jobs (id, kind, dedup_key, status, params, session_id, entry_id, "
                "created_at) VALUES (?, ?, ?, 'queued', ?, ?, ?, ?)",
                [jid, kind, dedup_key, json.dumps(params), session_id, entry_id, _now()],
            )
            row = c.execute("SELECT * FROM jobs WHERE id = ?", [jid]).fetchone()
        return Job.from_row(row), True

    def job(self, jid: str) -> Job | None:
        r = self.con().execute("SELECT * FROM jobs WHERE id = ?", [jid]).fetchone()
        return Job.from_row(r) if r else None

    def latest_job(self, dedup_key: str) -> Job | None:
        r = (
            self.con()
            .execute(
                "SELECT * FROM jobs WHERE dedup_key = ? ORDER BY created_at DESC LIMIT 1",
                [dedup_key],
            )
            .fetchone()
        )
        return Job.from_row(r) if r else None

    def queue_depth(self, kind: str | None = None) -> int:
        sql = "SELECT count(*) FROM jobs WHERE status = 'queued'"
        args: list = []
        if kind:
            sql += " AND kind = ?"
            args.append(kind)
        return int(self.con().execute(sql, args).fetchone()[0])

    def active_jobs_for(self, session_id: str) -> int:
        return int(
            self.con()
            .execute(
                "SELECT count(*) FROM jobs WHERE session_id = ? AND status IN ('queued','running')",
                [session_id],
            )
            .fetchone()[0]
        )

    def claim(self, worker: str, kinds: tuple[str, ...] = ("forecast", "plan")) -> Job | None:
        """Atomically take the oldest queued job (forecasts first: plans wait for them)."""
        marks = ",".join("?" for _ in kinds)
        with self.tx() as c:
            r = c.execute(
                f"SELECT id FROM jobs WHERE status = 'queued' AND kind IN ({marks}) "
                "ORDER BY CASE kind WHEN 'forecast' THEN 0 ELSE 1 END, created_at LIMIT 1",
                list(kinds),
            ).fetchone()
            if r is None:
                return None
            t = _now()
            c.execute(
                "UPDATE jobs SET status = 'running', started_at = ?, heartbeat = ?, worker = ?, "
                "attempts = attempts + 1 WHERE id = ? AND status = 'queued'",
                [t, t, worker, r["id"]],
            )
            row = c.execute("SELECT * FROM jobs WHERE id = ?", [r["id"]]).fetchone()
        return Job.from_row(row)

    def progress(self, jid: str, stage: str, progress: float, result: dict | None = None) -> None:
        with self.tx() as c:
            if result is None:
                c.execute(
                    "UPDATE jobs SET stage = ?, progress = ?, heartbeat = ? WHERE id = ?",
                    [stage, progress, _now(), jid],
                )
            else:
                c.execute(
                    "UPDATE jobs SET stage = ?, progress = ?, heartbeat = ?, result = ? "
                    "WHERE id = ?",
                    [stage, progress, _now(), json.dumps(result), jid],
                )

    def requeue(self, jid: str, stage: str) -> None:
        """Hand a claimed job back to the queue (it waits for something, e.g. its forecast)."""
        with self.tx() as c:
            c.execute(
                "UPDATE jobs SET status = 'queued', stage = ?, worker = NULL, "
                "attempts = max(attempts - 1, 0) WHERE id = ?",
                [stage, jid],
            )

    def finish(
        self, jid: str, status: str, result: dict | None = None, error: str | None = None
    ) -> None:
        assert status in ("done", "failed", "timed_out")
        with self.tx() as c:
            c.execute(
                "UPDATE jobs SET status = ?, result = COALESCE(?, result), error = ?, "
                "finished_at = ?, progress = CASE WHEN ? = 'done' THEN 1 ELSE progress END "
                "WHERE id = ?",
                [
                    status,
                    json.dumps(result) if result is not None else None,
                    error,
                    _now(),
                    status,
                    jid,
                ],
            )

    def recover_stale(self, older_than: float, max_attempts: int = 2) -> int:
        """Jobs whose worker stopped sending heartbeats: requeued once, then failed."""
        cut = _now() - older_than
        with self.tx() as c:
            rows = c.execute(
                "SELECT id, attempts FROM jobs WHERE status = 'running' AND heartbeat < ?", [cut]
            ).fetchall()
            for r in rows:
                if r["attempts"] < max_attempts:
                    c.execute(
                        "UPDATE jobs SET status = 'queued', worker = NULL WHERE id = ?", [r["id"]]
                    )
                else:
                    c.execute(
                        "UPDATE jobs SET status = 'failed', finished_at = ?, error = ? "
                        "WHERE id = ?",
                        [_now(), "the worker stopped responding", r["id"]],
                    )
        return len(rows)

    # ---- results ---------------------------------------------------------------------------
    def put_result(
        self,
        key: str,
        kind: str,
        stage: str,
        body: dict,
        *,
        entry_id: int | None = None,
        job_id: str | None = None,
    ) -> None:
        with self.tx() as c:
            c.execute(
                "INSERT INTO results (key, kind, entry_id, stage, created_at, job_id, body) "
                "VALUES (?, ?, ?, ?, ?, ?, ?) ON CONFLICT(key) DO UPDATE SET stage = "
                "excluded.stage, created_at = excluded.created_at, job_id = excluded.job_id, "
                "body = excluded.body",
                [key, kind, entry_id, stage, _now(), job_id, json.dumps(body, default=str)],
            )

    def result(self, key: str) -> dict | None:
        r = self.con().execute("SELECT * FROM results WHERE key = ?", [key]).fetchone()
        if r is None:
            return None
        return {
            "stage": r["stage"],
            "created_at": r["created_at"],
            "job_id": r["job_id"],
            "body": json.loads(r["body"]),
        }

    # ---- provider cache --------------------------------------------------------------------
    def cache_get(self, key: str) -> tuple[float, Any] | None:
        r = (
            self.con()
            .execute("SELECT fetched_at, body FROM provider_cache WHERE key = ?", [key])
            .fetchone()
        )
        return (r["fetched_at"], json.loads(r["body"])) if r else None

    def cache_put(self, key: str, body: Any, fetched_at: float | None = None) -> None:
        with self.tx() as c:
            c.execute(
                "INSERT INTO provider_cache (key, fetched_at, body) VALUES (?, ?, ?) "
                "ON CONFLICT(key) DO UPDATE SET fetched_at = excluded.fetched_at, "
                "body = excluded.body",
                [key, fetched_at or _now(), json.dumps(body)],
            )

    # ---- rate limits -----------------------------------------------------------------------
    def hit(self, bucket: str, limit: int, window_s: int) -> bool:
        """Count one use of `bucket` in the current fixed window; False when over `limit`."""
        w = int(_now() // window_s)
        with self.tx() as c:
            c.execute("DELETE FROM rate_limits WHERE bucket = ? AND window < ?", [bucket, w])
            r = c.execute(
                "SELECT count FROM rate_limits WHERE bucket = ? AND window = ?", [bucket, w]
            ).fetchone()
            n = (r["count"] if r else 0) + 1
            if n > limit:
                return False
            c.execute(
                "INSERT INTO rate_limits (bucket, window, count) VALUES (?, ?, ?) "
                "ON CONFLICT(bucket, window) DO UPDATE SET count = excluded.count",
                [bucket, w, n],
            )
        return True

    # ---- forecasts and data revisions ------------------------------------------------------
    def put_forecast(self, key: str, revision: str, path: str, manifest: dict) -> None:
        with self.tx() as c:
            c.execute(
                "INSERT OR REPLACE INTO forecasts (key, revision, created_at, path, "
                "manifest) VALUES (?, ?, ?, ?, ?)",
                [key, revision, _now(), path, json.dumps(manifest, default=str)],
            )

    def forecast(self, key: str) -> dict | None:
        r = self.con().execute("SELECT * FROM forecasts WHERE key = ?", [key]).fetchone()
        if r is None:
            return None
        return {
            "key": r["key"],
            "revision": r["revision"],
            "created_at": r["created_at"],
            "path": r["path"],
            "manifest": json.loads(r["manifest"]),
        }

    def latest_forecast(self, revision: str | None = None) -> dict | None:
        sql, args = "SELECT key FROM forecasts", []
        if revision:
            sql += " WHERE revision = ?"
            args.append(revision)
        r = self.con().execute(sql + " ORDER BY created_at DESC LIMIT 1", args).fetchone()
        return self.forecast(r["key"]) if r else None

    def add_revision(self, rid: str, path: str, meta: dict, make_current: bool = True) -> None:
        with self.tx() as c:
            if make_current:
                c.execute("UPDATE revisions SET current = 0")
            c.execute(
                "INSERT OR REPLACE INTO revisions (id, path, created_at, meta, current) "
                "VALUES (?, ?, ?, ?, ?)",
                [rid, path, _now(), json.dumps(meta, default=str), 1 if make_current else 0],
            )

    def current_revision(self) -> dict | None:
        r = self.con().execute("SELECT * FROM revisions WHERE current = 1 LIMIT 1").fetchone()
        if r is None:
            return None
        return {
            "id": r["id"],
            "path": r["path"],
            "created_at": r["created_at"],
            "meta": json.loads(r["meta"]),
        }


__all__ = ["Store", "Job", "ACTIVE"]
