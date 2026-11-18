"""Run the website on your own computer for weekly use (`fpl-agent web local`).

One command, the same on Windows, macOS and Linux, with no bash, gh or zstd binary needed:

1. **Refresh the football data.** The `data` release of this (public) repository holds the
   database export that CI refreshes daily and in full each week. When its asset is newer than
   the copy imported last time, it is downloaded, unpacked and imported into a new database file,
   checked, and swapped in. If GitHub can't be reached, the existing database is used and the
   launcher says how old it is. Nothing is uploaded anywhere.
2. **Publish a revision** (`web publish --ownership`) when the database changed, or the revision
   was captured before the last deadline passed, and remove older revisions (each is a full copy
   of the database) so the disk does not fill up week after week.
3. **Build the site** when its source or lockfile changed since the last build.
4. **Start** the API, the workers and the site, wait for their health checks, queue the plan for
   your team so it is computing while the page opens, and open the browser on your team.

The website stays read-only: it never signs in to FPL and never changes a team.
"""

from __future__ import annotations

import hashlib
import http.cookiejar
import json
import os
import shutil
import signal
import subprocess
import sys
import tarfile
import time
import urllib.error
import urllib.request
import webbrowser
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

REPO = os.environ.get("WEB_DATA_REPO", "OPEXAI-GG/fpl-ai-agent")
ASSET = "football-db.tar.zst"
UA = {"User-Agent": "fpl-agent-local/1.0", "Accept": "application/vnd.github+json"}
STATE_FILE = "local_state.json"
KEEP_REVISIONS = 1  # each revision is a full copy of the database (about 2 GB)


def say(msg: str) -> None:
    print(f"[matchplan] {msg}", flush=True)


# ---- state --------------------------------------------------------------------------------------
def _state_path(data_dir: Path) -> Path:
    return Path(data_dir) / STATE_FILE


def load_state(data_dir: Path) -> dict:
    p = _state_path(data_dir)
    try:
        return json.loads(p.read_text())
    except (OSError, ValueError):
        return {}


def save_state(data_dir: Path, state: dict) -> None:
    p = _state_path(data_dir)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=2))
    os.replace(tmp, p)


# ---- data release -------------------------------------------------------------------------------
@dataclass(frozen=True)
class Asset:
    url: str
    size: int
    updated_at: str  # ISO time GitHub gives for the upload

    @property
    def tag(self) -> str:
        return f"{self.updated_at}|{self.size}"


def release_asset(repo: str = REPO, timeout: float = 20) -> Asset:
    """The current data export on the public `data` release (no sign-in needed)."""
    req = urllib.request.Request(
        f"https://api.github.com/repos/{repo}/releases/tags/data", headers=UA
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        rel = json.load(r)
    for a in rel.get("assets", []):
        if a.get("name") == ASSET:
            return Asset(a["browser_download_url"], int(a["size"]), a["updated_at"])
    raise LookupError(f"the data release has no {ASSET}")


def download(asset: Asset, dest: Path, timeout: float = 60) -> Path:
    """Stream the asset to `dest`, checking the size GitHub reported."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".part")
    req = urllib.request.Request(asset.url, headers={"User-Agent": UA["User-Agent"]})
    got, last = 0, 0.0
    with urllib.request.urlopen(req, timeout=timeout) as r, open(tmp, "wb") as f:
        while chunk := r.read(1 << 20):
            f.write(chunk)
            got += len(chunk)
            if time.time() - last > 2:
                say(f"  downloaded {got / 1e6:.0f} of {asset.size / 1e6:.0f} MB")
                last = time.time()
    if got != asset.size:
        tmp.unlink(missing_ok=True)
        raise OSError(f"download incomplete: {got} of {asset.size} bytes")
    os.replace(tmp, dest)
    return dest


def unpack(archive: Path, out: Path) -> Path:
    """Extract a .tar.zst export (parquet files) into an empty `out` directory."""
    import zstandard

    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)
    with open(archive, "rb") as f:
        reader = zstandard.ZstdDecompressor().stream_reader(f)
        with tarfile.open(fileobj=reader, mode="r|") as tar:
            tar.extractall(out, filter="data")
    if not any(out.glob("*.parquet")):
        raise ValueError("the export has no tables")
    return out


def import_export(export_dir: Path, target: Path, season: int) -> Path:
    """Import parquet tables into a fresh database next to `target`, check it, then swap it in.
    The previous database stays in place until the new one is complete and readable."""
    from fpl_agent.db.database import FootballDB
    from fpl_agent.web import snapshots

    new = target.with_name(target.name + ".new")
    for p in (new, new.with_name(new.name + ".wal")):
        p.unlink(missing_ok=True)
    db = FootballDB(new)
    try:
        db.import_parquet(export_dir)
    finally:
        db.close()
    check = FootballDB.read_only(new)
    try:
        meta = snapshots.describe(check, season)
    finally:
        check.close()
    if not meta.get("fpl_snapshot_at"):
        new.unlink(missing_ok=True)
        raise ValueError(f"the imported database has no FPL snapshot for season {season}")
    os.replace(new, target)
    return target


def refresh_data(settings, force: bool = False) -> dict:
    """Bring the working database up to the latest release. Returns what happened; never raises
    for a network problem when a database already exists."""
    data_dir, db_path = Path(settings.data_dir), Path(settings.source_db)
    state = load_state(data_dir)
    try:
        asset = release_asset()
    except (OSError, urllib.error.URLError, ValueError, LookupError) as exc:
        if db_path.exists():
            say(f"could not check for new data ({type(exc).__name__}); using the data you have")
            return {"changed": False, "offline": True, "asset_updated_at": state.get("asset_tag")}
        raise RuntimeError(
            "No football database yet and GitHub could not be reached to download one. "
            "Check your internet connection and run this again."
        ) from exc
    if db_path.exists() and state.get("asset_tag") == asset.tag and not force:
        say(f"football data is current (exported {asset.updated_at})")
        return {"changed": False, "offline": False, "asset_updated_at": asset.updated_at}
    say(f"downloading new football data (exported {asset.updated_at}, {asset.size / 1e6:.0f} MB)")
    work = db_path.parent / "local_download"
    archive = download(asset, work / ASSET)
    say("unpacking and importing (a minute or two)")
    export = unpack(archive, work / "export")
    db_path.parent.mkdir(parents=True, exist_ok=True)
    import_export(export, db_path, settings.season)
    shutil.rmtree(work, ignore_errors=True)
    state["asset_tag"] = asset.tag
    state["imported_at"] = datetime.now(UTC).isoformat()
    save_state(data_dir, state)
    say("football data updated")
    return {"changed": True, "offline": False, "asset_updated_at": asset.updated_at}


# ---- revisions ----------------------------------------------------------------------------------
def needs_publish(svc, data_changed: bool, now: datetime | None = None) -> str | None:
    """Why a new revision is needed, or None. A revision is republished when the database changed,
    when none exists, or when the deadline it was captured for has passed."""
    if data_changed:
        return "the football data changed"
    try:
        rev = svc.revision()
    except Exception:  # noqa: BLE001  (no revision, or its files were removed)
        return "no published revision yet"
    boot = rev.captured("bootstrap") or {}
    nxt = next((e for e in boot.get("events", []) if e.get("is_next")), None)
    if nxt is None:
        return "the revision has no upcoming deadline"
    deadline = datetime.fromisoformat(nxt["deadline_time"].replace("Z", "+00:00"))
    if (now or datetime.now(UTC)) >= deadline:
        return f"the GW{nxt['id']} deadline has passed since it was captured"
    return None


def prune_revisions(root: Path, keep: set[str], max_keep: int = KEEP_REVISIONS) -> list[str]:
    """Delete published revisions beyond the newest `max_keep`, never one in `keep`."""
    snaps = Path(root) / "snapshots"
    if not snaps.exists():
        return []
    dirs = sorted(
        (d for d in snaps.iterdir() if d.is_dir() and d.name.startswith("r")),
        key=lambda d: d.stat().st_mtime,
        reverse=True,
    )
    removed = []
    for d in dirs[max_keep:]:
        if d.name in keep:
            continue
        shutil.rmtree(d, ignore_errors=True)
        removed.append(d.name)
    for d in snaps.iterdir():  # interrupted copies
        if d.name.startswith(".") and d.name.endswith(".tmp"):
            shutil.rmtree(d, ignore_errors=True)
    return removed


# ---- site build ---------------------------------------------------------------------------------
def _source_stamp(web: Path) -> str:
    h = hashlib.sha256()
    skip = {"node_modules", ".next", "e2e"}
    for p in sorted(web.rglob("*")):
        rel = p.relative_to(web)
        if rel.parts and rel.parts[0] in skip or not p.is_file():
            continue
        h.update(str(rel.as_posix()).encode())
        h.update(p.read_bytes())
    return h.hexdigest()[:16]


def _npm(*args: str) -> list[str]:
    names = ("npm.cmd", "npm") if os.name == "nt" else ("npm",)
    exe = next((w for n in names if (w := shutil.which(n))), None)
    if not exe:
        raise RuntimeError("Node.js (npm) was not found. Install Node.js 22 or newer.")
    return [exe, *args]


def build_site(web: Path, data_dir: Path, api_url: str, log: Path) -> None:
    state = load_state(data_dir)
    lock = hashlib.sha256((web / "package-lock.json").read_bytes()).hexdigest()[:16]
    env = {**os.environ, "API_URL": api_url, "NEXT_TELEMETRY_DISABLED": "1"}
    with open(log, "a") as out:
        if state.get("npm_lock") != lock or not (web / "node_modules").exists():
            say("installing website packages (first run or after an update)")
            subprocess.run(
                _npm("ci", "--no-audit", "--no-fund", "--loglevel=error"),
                cwd=web,
                env=env,
                stdout=out,
                stderr=subprocess.STDOUT,
                check=True,
            )
            state["npm_lock"] = lock
            state.pop("site_stamp", None)
            save_state(data_dir, state)
        stamp = _source_stamp(web) + "|" + api_url
        if state.get("site_stamp") != stamp or not (web / ".next" / "BUILD_ID").exists():
            say("building the website (about a minute)")
            subprocess.run(
                _npm("run", "build"),
                cwd=web,
                env=env,
                stdout=out,
                stderr=subprocess.STDOUT,
                check=True,
            )
            state["site_stamp"] = stamp
            save_state(data_dir, state)


# ---- processes ----------------------------------------------------------------------------------
def _spawn(cmd: list[str], log: Path, cwd: Path, env: dict) -> subprocess.Popen:
    kw: dict = {}
    if os.name == "nt":
        kw["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        kw["start_new_session"] = True
    out = open(log, "a")  # noqa: SIM115  (kept open for the child's lifetime)
    return subprocess.Popen(cmd, cwd=cwd, env=env, stdout=out, stderr=subprocess.STDOUT, **kw)


def _stop(procs: list[subprocess.Popen]) -> None:
    for p in procs:
        if p.poll() is not None:
            continue
        try:
            if os.name == "nt":
                subprocess.run(
                    ["taskkill", "/T", "/F", "/PID", str(p.pid)],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    check=False,
                )
            else:
                os.killpg(p.pid, signal.SIGTERM)
        except OSError:
            pass
    for p in procs:
        try:
            p.wait(timeout=10)
        except subprocess.TimeoutExpired:
            p.kill()


def _wait_http(url: str, seconds: int, procs: list[subprocess.Popen]) -> bool:
    end = time.time() + seconds
    while time.time() < end:
        if any(p.poll() is not None for p in procs):
            return False
        try:
            with urllib.request.urlopen(url, timeout=3) as r:
                if r.status == 200:
                    return True
        except (OSError, urllib.error.URLError):
            pass
        time.sleep(1)
    return False


def warm_plan(site: str, entry: int) -> str:
    """Queue the plan for `entry` through the site (default settings, the same cache key a fresh
    browser uses) so it is already computing when the page opens."""
    jar = http.cookiejar.CookieJar()
    op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
    body = json.dumps({"query": str(entry)}).encode()
    req = urllib.request.Request(
        f"{site}/api/lookup", data=body, headers={"content-type": "application/json"}
    )
    op.open(req, timeout=30).read()
    with op.open(
        urllib.request.Request(f"{site}/api/entries/{entry}/plan", data=b""), timeout=60
    ) as r:
        return json.load(r).get("status", "?")


def run(args) -> int:
    from fpl_agent.web.config import WebSettings

    root = Path(__file__).resolve().parents[3]
    os.chdir(root)
    os.environ.setdefault("WEB_PLAN_BUDGET_S", "2400")  # one manager's machine: let plans finish
    s = WebSettings.from_env()
    logs = s.data_dir / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    state = load_state(s.data_dir)
    entry = args.entry or state.get("entry")
    if entry:
        state["entry"] = int(entry)
        save_state(s.data_dir, state)
    page = f"http://localhost:{args.site_port}/" + (f"team/{entry}" if entry else "")
    if _wait_http(f"http://127.0.0.1:{args.site_port}/api/health", 1, []):
        say(f"the website is already running: {page}")
        if not args.no_browser:
            webbrowser.open(page)
        return 0

    say("checking for new football data")
    try:
        res = (
            refresh_data(s, force=args.force_refresh) if not args.no_refresh else {"changed": False}
        )
    except Exception as exc:  # noqa: BLE001
        say(f"could not prepare the football data: {exc}")
        return 2

    from fpl_agent.web.cli import publish as publish_cmd
    from fpl_agent.web.service import Service

    svc = Service.create(s)
    why = needs_publish(svc, res.get("changed", False))
    if why:
        say(f"publishing a fresh revision ({why}); this reads FPL's public data")

        class _A:
            update = False
            ownership = True
            no_forecast = False

        if publish_cmd(svc, _A()) != 0:
            say("FPL could not be reached to publish; using the last revision")
    rev = svc.store.current_revision()
    if rev is None:
        say("no revision could be published; check your internet connection and try again")
        return 2
    removed = prune_revisions(s.data_dir, keep={rev["id"]})
    if removed:
        say(f"removed {len(removed)} old revision(s) to save disk space")
    snap = rev["meta"].get("fpl_snapshot_at", "?")
    say(f"data revision {rev['id']} (FPL data from {snap[:16].replace('T', ' ')} UTC)")

    api = f"http://127.0.0.1:{args.port}"
    site = f"http://127.0.0.1:{args.site_port}"
    web = root / "web"
    try:
        build_site(web, s.data_dir, api, logs / "web-build.log")
    except (subprocess.CalledProcessError, RuntimeError) as exc:
        say(f"the website could not be built: {exc}. See {logs / 'web-build.log'}")
        return 2

    env = {**os.environ, "API_URL": api, "NEXT_TELEMETRY_DISABLED": "1", "PYTHONUNBUFFERED": "1"}
    py = sys.executable
    procs: list[subprocess.Popen] = []
    try:
        procs.append(
            _spawn(
                [py, "-m", "fpl_agent.cli", "web", "serve", "--port", str(args.port)],
                logs / "api.log",
                root,
                env,
            )
        )
        for i in range(max(1, args.workers)):
            procs.append(
                _spawn(
                    [py, "-m", "fpl_agent.cli", "web", "worker"],
                    logs / f"worker{i + 1}.log",
                    root,
                    env,
                )
            )
        nxt = shutil.which("node.exe" if os.name == "nt" else "node") or shutil.which("node")
        procs.append(
            _spawn(
                [
                    nxt or "node",
                    str(web / "node_modules" / "next" / "dist" / "bin" / "next"),
                    "start",
                    "-p",
                    str(args.site_port),
                    "-H",
                    "127.0.0.1",
                ],
                logs / "web.log",
                web,
                env,
            )
        )
        if not _wait_http(f"{api}/api/health", 90, procs):
            say(f"the API did not start; see {logs / 'api.log'}")
            return 1
        if not _wait_http(f"{site}/api/health", 120, procs):
            say(f"the website did not start; see {logs / 'web.log'}")
            return 1
        url = f"http://localhost:{args.site_port}/"
        if entry:
            try:
                say(f"plan for team {entry}: {warm_plan(site, int(entry))}")
            except (OSError, urllib.error.URLError, ValueError) as exc:
                say(f"could not queue your plan yet ({exc}); open the Plan tab to start it")
            url = f"http://localhost:{args.site_port}/team/{entry}"
        say(f"ready: {url}")
        say("leave this window open while you use the site; close it (or Ctrl+C) to stop")
        if not args.no_browser:
            webbrowser.open(url)
        while all(p.poll() is None for p in procs):
            time.sleep(2)
        say(f"a service stopped unexpectedly; see the logs in {logs}")
        return 1
    except KeyboardInterrupt:
        say("stopping")
        return 0
    finally:
        _stop(procs)


def add_arguments(p) -> None:
    p.add_argument("--entry", type=int, help="local: your FPL team ID (remembered)")
    p.add_argument("--workers", type=int, default=1, help="local: plan workers (1-2)")
    p.add_argument("--no-refresh", action="store_true", help="local: skip the data download")
    p.add_argument("--force-refresh", action="store_true", help="local: re-download the data")
    p.add_argument("--no-browser", action="store_true", help="local: don't open the browser")
    p.add_argument("--site-port", type=int, default=3000, help="local: the website's port")


__all__ = ["run", "refresh_data", "needs_publish", "prune_revisions", "release_asset"]
