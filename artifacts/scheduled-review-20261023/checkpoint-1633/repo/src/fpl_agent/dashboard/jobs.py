"""The dashboard's controls: a fixed list of safe actions, each run as the existing CLI in a
child process (argument list, no shell), one at a time.

Every job is forced dry whatever the server inherited: AGENT_MODE=dry, notifications off, and
the FPL sign-in, GitHub and webhook credentials are removed from the child's environment, so a
job cannot sign in to FPL, post to Discord or touch GitHub. Optional model/odds keys are passed
only when the person starting the dashboard opted in (`--allow-model-keys`). There is no live
transfer, token, shell, deployment or secret action.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
import uuid
from collections import deque
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

MAX_OUTPUT = 64 * 1024  # characters of output kept per job
TIMEOUT_S = {"refresh": 30 * 60, "plan": 30 * 60, "review": 10 * 60}
# never handed to a job: FPL sign-in, GitHub and delivery credentials
STRIPPED = ("FPL_REFRESH_TOKEN", "FPL_TOKEN_URL", "FPL_CLIENT_ID", "GH_TOKEN", "GITHUB_TOKEN",
            "GH_PAT", "NOTIFY_WEBHOOK", "DISCORD_TOKEN", "DISCORD_WEBHOOK",
            "DISCORD_BOT_TOKEN")  # fmt: skip
MODEL_KEYS = ("ANTHROPIC_API_KEY", "ODDS_API_KEY")
LIMITS = {"season": (2016, 2100), "horizon": (1, 8), "samples": (50, 5000),
          "entry_id": (1, 99_999_999)}  # fmt: skip
DEFAULTS = {"season": 2026, "horizon": 8, "samples": 1000, "entry_id": None}


class Rejected(ValueError):
    """A request the dashboard refuses (shown to the browser as a 4xx)."""


def validate_prefs(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise Rejected("preferences must be an object")
    out = dict(DEFAULTS)
    for key, value in raw.items():
        if key not in LIMITS:
            raise Rejected(f"unknown setting {key!r}")
        if value is None or value == "":
            if key != "entry_id":
                raise Rejected(f"{key} is required")
            out[key] = None
            continue
        if isinstance(value, bool) or not isinstance(value, int | str):
            raise Rejected(f"{key} must be a whole number")
        try:
            n = int(value)
        except ValueError as exc:
            raise Rejected(f"{key} must be a whole number") from exc
        lo, hi = LIMITS[key]
        if not lo <= n <= hi:
            raise Rejected(f"{key} must be between {lo} and {hi}")
        out[key] = n
    return out


@dataclass
class Job:
    id: str
    action: str
    args: list[str]
    status: str = "queued"  # queued | running | succeeded | failed | cancelled | interrupted
    created_at: str = ""
    started_at: str | None = None
    ended_at: str | None = None
    exit_code: int | None = None
    output: str = ""
    message: str = ""
    cancelling: bool = False
    pid: int | None = field(default=None, repr=False)

    def public(self) -> dict[str, Any]:
        d = asdict(self)
        d.pop("pid")
        return d


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


class Jobs:
    """Starts, tracks, persists and cancels jobs. One job may write at a time: the lock is a
    file created exclusively, so a second dashboard on the same database is refused too."""

    def __init__(self, db_path: Path, state_dir: Path, allow_model_keys: bool = False,
                 python: str = sys.executable) -> None:  # fmt: skip
        self.db_path = Path(db_path)
        self.state_dir = Path(state_dir)
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.allow_model_keys = allow_model_keys
        self.python = python
        self._lock = threading.Lock()
        self._jobs: dict[str, Job] = {}
        self._proc: dict[str, subprocess.Popen] = {}
        self._load()

    # ---- persistence ---------------------------------------------------------------------
    @property
    def _file(self) -> Path:
        return self.state_dir / "jobs.json"

    @property
    def lock_file(self) -> Path:
        return self.state_dir / "writer.lock"

    @property
    def prefs_file(self) -> Path:
        return self.state_dir / "preferences.json"

    def _load(self) -> None:
        try:
            data = json.loads(self._file.read_text(encoding="utf-8"))
        except (FileNotFoundError, ValueError):
            data = []
        holder = _lock_holder(self.lock_file)
        for d in data[-50:]:
            try:
                job = Job(**{k: v for k, v in d.items() if k in Job.__dataclass_fields__})
            except TypeError:
                continue
            if job.status in ("queued", "running") and holder is None:
                job.status, job.ended_at = "interrupted", job.ended_at or _now()
                job.message = "the dashboard stopped while this job was running"
            self._jobs[job.id] = job
        if holder is None:
            self.lock_file.unlink(missing_ok=True)  # left by a dashboard that was killed

    def _save(self) -> None:
        """Write the job list atomically. Windows may briefly refuse the rename while another
        program (a virus scanner, a reader) has the file open: retry, and never let a failed
        save stop a job."""
        jobs = sorted(self._jobs.values(), key=lambda j: j.created_at)[-50:]
        tmp = self._file.with_name(f"jobs.{os.getpid()}.{threading.get_ident()}.tmp")
        tmp.write_text(json.dumps([j.public() for j in jobs], indent=1), encoding="utf-8")
        for attempt in range(20):
            try:
                tmp.replace(self._file)
                return
            except PermissionError:
                time.sleep(0.05 * (attempt + 1))
        tmp.unlink(missing_ok=True)

    def prefs(self) -> dict[str, Any]:
        try:
            return validate_prefs(json.loads(self.prefs_file.read_text(encoding="utf-8")))
        except (FileNotFoundError, ValueError):
            return dict(DEFAULTS)

    def save_prefs(self, raw: Any) -> dict[str, Any]:
        prefs = validate_prefs(raw)
        self.prefs_file.write_text(json.dumps(prefs, indent=1), encoding="utf-8")
        return prefs

    # ---- actions ---------------------------------------------------------------------------
    def command(self, action: str, prefs: dict[str, Any]) -> list[str]:
        """The fixed argument list for an action. Nothing from the browser is passed except
        validated numbers."""
        db, season = str(self.db_path), str(prefs["season"])
        base = [self.python, "-m", "fpl_agent.cli"]
        if action == "refresh":
            return [*base, "db-update", "--db", db, "--season", season, "--football-data",
                    "E0", "--points", "--fotmob", "ENG1,UCL,UEL,UECL",
                    "--fotmob-matches", "40"]  # fmt: skip
        if action == "plan":
            if prefs.get("entry_id") is None:
                raise Rejected("set your FPL team id in Settings before a dry plan")
            return [*base, "plan", "--db", db, "--season", season, "--entry",
                    str(prefs["entry_id"]), "--horizon", str(prefs["horizon"]), "--samples",
                    str(prefs["samples"]), "--force", "--out",
                    str(self.state_dir / "plans")]  # fmt: skip
        if action == "review":
            return [*base, "review", "--db", db, "--season", season]
        raise Rejected(f"unknown action {action!r}")

    def env(self) -> dict[str, str]:
        env = {k: v for k, v in os.environ.items() if k not in STRIPPED}
        if not self.allow_model_keys:
            for k in MODEL_KEYS:
                env.pop(k, None)
        env.update(AGENT_MODE="dry", AGENT_ENABLED="true", PYTHONIOENCODING="utf-8",
                   PYTHONUTF8="1")  # fmt: skip
        env.pop("FPL_ENTRY_ID", None)  # the entry comes from the dashboard's own settings
        return env

    def busy(self) -> bool:
        with self._lock:
            return any(j.status in ("queued", "running") for j in self._jobs.values())

    def start(self, action: str) -> Job:
        args = self.command(action, self.prefs())
        with self._lock:
            if any(j.status in ("queued", "running") for j in self._jobs.values()):
                raise Busy("another job is still running")
            try:
                fd = os.open(self.lock_file, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            except FileExistsError as exc:
                raise Busy("another dashboard or job holds the database") from exc
            os.write(fd, str(os.getpid()).encode())
            os.close(fd)
            job = Job(uuid.uuid4().hex[:12], action, args[3:], created_at=_now())
            self._jobs[job.id] = job
            self._save()
        threading.Thread(target=self._run, args=(job, args), daemon=True).start()
        return job

    def _run(self, job: Job, args: list[str]) -> None:
        tail: deque[str] = deque()
        size = 0
        try:
            proc = subprocess.Popen(
                args, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                env=self.env(), shell=False, text=True, encoding="utf-8", errors="replace",
            )  # fmt: skip
        except OSError as exc:
            self._finish(job, "failed", None, f"could not start: {type(exc).__name__}", "")
            return
        with self._lock:
            job.status, job.started_at, job.pid = "running", _now(), proc.pid
            self._proc[job.id] = proc
            self._save()
        timer = threading.Timer(TIMEOUT_S[job.action], self._timeout, args=(job.id,))
        timer.daemon = True
        timer.start()
        assert proc.stdout is not None
        for line in proc.stdout:
            tail.append(line)
            size += len(line)
            while size > MAX_OUTPUT and len(tail) > 1:
                size -= len(tail.popleft())
            with self._lock:
                job.output = "".join(tail)
        code = proc.wait()
        timer.cancel()
        with self._lock:
            cancelled = job.cancelling
        if cancelled:
            self._finish(job, "cancelled", code, job.message or "cancelled", "".join(tail))
        else:
            ok = code == 0
            self._finish(job, "succeeded" if ok else "failed", code,
                         "" if ok else f"exit code {code}", "".join(tail))  # fmt: skip

    def _finish(self, job: Job, status: str, code: int | None, msg: str, output: str) -> None:
        with self._lock:
            job.status, job.exit_code, job.ended_at = status, code, _now()
            job.message, job.output = msg, output[-MAX_OUTPUT:]
            self._proc.pop(job.id, None)
            self._save()
            self.lock_file.unlink(missing_ok=True)

    def _timeout(self, job_id: str) -> None:
        self.cancel(job_id, f"stopped after {TIMEOUT_S[self._jobs[job_id].action] // 60} min")

    def cancel(self, job_id: str, why: str = "cancelled from the dashboard") -> Job:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                raise Rejected("no such job")
            proc = self._proc.get(job_id)
            if job.status != "running" or proc is None:
                raise Rejected(f"job is {job.status}, not running")
            job.cancelling, job.message = True, why
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
        return job

    def get(self, job_id: str) -> Job | None:
        with self._lock:
            return self._jobs.get(job_id)

    def list(self) -> list[dict[str, Any]]:
        with self._lock:
            jobs = sorted(self._jobs.values(), key=lambda j: j.created_at, reverse=True)
            return [{**j.public(), "output": j.output[-2000:]} for j in jobs[:20]]

    def wait(self, job_id: str, timeout: float = 30.0) -> Job:
        """For tests and the CLI: block until the job ends."""
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            job = self.get(job_id)
            if job and job.ended_at is not None:
                return job
            time.sleep(0.05)
        raise TimeoutError(job_id)


def _lock_holder(lock: Path) -> int | None:
    """The process id holding the writer lock, if that process is still alive."""
    try:
        pid = int(lock.read_text(encoding="utf-8").strip())
    except (FileNotFoundError, ValueError):
        return None
    return pid if _alive(pid) else None


def _alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if os.name == "nt":  # os.kill would terminate the process on Windows: ask instead
        import ctypes

        k32 = ctypes.windll.kernel32
        handle = k32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not handle:
            return False
        code = ctypes.c_ulong()
        ok = k32.GetExitCodeProcess(handle, ctypes.byref(code))
        k32.CloseHandle(handle)
        return bool(ok) and code.value == 259  # STILL_ACTIVE
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


class Busy(RuntimeError):
    """A write job is already running (shown to the browser as 409)."""
