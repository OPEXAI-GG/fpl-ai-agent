"""The dashboard's web server: Python's own WSGI server on 127.0.0.1 only, a JSON API and three
static files. Single user, local machine; not for hosting.

Guards: requests must name this server in Host (DNS-rebinding), a POST must come from this
page (Origin) and carry the per-start token the page was served with, bodies are capped, and
every response forbids framing and outside scripts. No route takes a file path, SQL or a
command; only the fixed actions in jobs.py exist.
"""

from __future__ import annotations

import json
import secrets
import threading
from datetime import UTC, datetime
from importlib import resources
from pathlib import Path
from socketserver import ThreadingMixIn
from typing import Any
from urllib.parse import parse_qs
from wsgiref.simple_server import WSGIRequestHandler, WSGIServer, make_server

from fpl_agent.dashboard.jobs import Busy, Jobs, Rejected
from fpl_agent.dashboard.service import STALE_SNAPSHOT_HOURS, ReadModel, settings_presence

HOST = "127.0.0.1"
MAX_BODY = 4096
STATIC = {"/": ("index.html", "text/html; charset=utf-8"),
          "/app.js": ("app.js", "text/javascript; charset=utf-8"),
          "/styles.css": ("styles.css", "text/css; charset=utf-8")}  # fmt: skip
SECTIONS = ("overview", "team", "recommendations", "players", "planning", "activity", "health")
HEADERS = [
    ("Content-Security-Policy", "default-src 'self'; img-src 'self' data:; "
     "frame-ancestors 'none'; base-uri 'none'; form-action 'none'"),
    ("X-Frame-Options", "DENY"),
    ("X-Content-Type-Options", "nosniff"),
    ("Referrer-Policy", "no-referrer"),
    ("Cache-Control", "no-store"),
]  # fmt: skip
PAGE_SIZES = (10, 25, 50, 100)


class App:
    def __init__(self, db_path: Path, state_dir: Path, port: int,
                 allow_model_keys: bool = False, jobs: Jobs | None = None) -> None:  # fmt: skip
        self.token = secrets.token_urlsafe(24)
        self.jobs = jobs or Jobs(db_path, state_dir, allow_model_keys)
        self.model = ReadModel(db_path, busy=self.jobs.busy)
        self._static: dict[str, bytes] = {}
        self.bind(port)

    def bind(self, port: int) -> None:
        """The port the server really listens on (it may have been 0: any free port)."""
        self.port = port
        self.hosts = {f"{HOST}:{port}", f"localhost:{port}"}
        self.origins = {f"http://{h}" for h in self.hosts}

    # ---- WSGI ------------------------------------------------------------------------------
    def __call__(self, environ: dict, start_response) -> list[bytes]:
        try:
            status, body, ctype = self.route(environ)
        except Forbidden as exc:
            status, body, ctype = _json(403, {"error": str(exc)})
        except Rejected as exc:
            status, body, ctype = _json(400, {"error": str(exc)})
        except Busy as exc:
            status, body, ctype = _json(409, {"error": str(exc)})
        except Exception as exc:  # noqa: BLE001 - never leak a traceback to the page
            status, body, ctype = _json(500, {"error": f"server error ({type(exc).__name__})"})
        start_response(status, [("Content-Type", ctype), ("Content-Length", str(len(body))),
                                *HEADERS])  # fmt: skip
        return [body]

    def route(self, env: dict) -> tuple[str, bytes, str]:
        if env.get("HTTP_HOST") not in self.hosts:
            return _json(421, {"error": "unexpected Host header"})
        method, path = env["REQUEST_METHOD"], env.get("PATH_INFO") or "/"
        if method == "GET":
            if path in STATIC:
                return self.static(path)
            if path.startswith("/api/jobs/"):
                job = self.jobs.get(path.rsplit("/", 1)[1])
                return _json(200, job.public()) if job else _json(404, {"error": "no such job"})
            if path == "/api/jobs":
                return _json(200, {"items": self.jobs.list(), "busy": self.jobs.busy()})
            if path == "/api/preferences":
                return _json(200, self.jobs.prefs())
            if path.startswith("/api/") and path[5:] in SECTIONS:
                return _json(200, self.section(path[5:], parse_qs(env.get("QUERY_STRING", ""))))
            return _json(404, {"error": "not found"})
        if method == "POST":
            self.check_post(env)
            body = _body(env)
            if path == "/api/jobs":
                action = body.get("action")
                if not isinstance(action, str):
                    raise Rejected("action is required")
                return _json(202, self.jobs.start(action).public())
            if path.startswith("/api/jobs/") and path.endswith("/cancel"):
                return _json(200, self.jobs.cancel(path.split("/")[3]).public())
            if path == "/api/preferences":
                if self.jobs.busy():
                    raise Busy("settings can change once the running job ends")
                return _json(200, self.jobs.save_prefs(body))
            return _json(404, {"error": "not found"})
        return _json(405, {"error": "method not allowed"})

    def check_post(self, env: dict) -> None:
        if env.get("HTTP_ORIGIN") not in self.origins:
            raise Forbidden("cross-origin request refused")
        if not secrets.compare_digest(env.get("HTTP_X_DASHBOARD_TOKEN", ""), self.token):
            raise Forbidden("missing or wrong dashboard token")

    def static(self, path: str) -> tuple[str, bytes, str]:
        name, ctype = STATIC[path]
        if name not in self._static:
            data = resources.files("fpl_agent.dashboard").joinpath("static", name).read_bytes()
            if name == "index.html":  # the page carries this run's control token
                data = data.replace(b"__DASHBOARD_TOKEN__", self.token.encode())
            self._static[name] = data
        return "200 OK", self._static[name], ctype

    # ---- sections --------------------------------------------------------------------------
    def section(self, name: str, q: dict[str, list[str]]) -> dict[str, Any]:
        snap, flags = self.model.get()
        meta = {"state": snap.state, "message": snap.message, "captured_at": snap.built_at,
                "build_ms": snap.build_ms, **flags}  # fmt: skip
        data = snap.sections.get(name)
        if data and name in ("overview", "health"):
            # Freshness must advance even when the database file has not changed.
            at = data.get("snapshot_at")
            age = (
                (datetime.now(UTC) - datetime.fromisoformat(at)).total_seconds() / 3600
                if at
                else None
            )
            data = {
                **data,
                "snapshot_age_hours": round(age, 1) if age is not None else None,
                "snapshot_stale": age is None or age > STALE_SNAPSHOT_HOURS,
            }
        if name == "health":
            data = data or {"settings": settings_presence()}
        if name == "players" and data:
            data = filter_players(data, q)
        return {"meta": meta, "data": data}


class Forbidden(Rejected):
    pass


def filter_players(data: dict[str, Any], q: dict[str, list[str]]) -> dict[str, Any]:
    """Search, filter, sort and page the snapshot's players (all in memory, no SQL)."""
    one = lambda k, d="": (q.get(k) or [d])[0]  # noqa: E731
    items = data["items"]
    ids = {int(x) for x in one("ids").split(",") if x.strip().isdigit()}
    if ids:
        items = [p for p in items if p["player_id"] in ids][:4]
        return {**data, "items": items, "total": len(items), "offset": 0, "limit": 4}
    text = one("q").strip().lower()[:40]
    if text:
        items = [p for p in items if text in (p["name"] or "").lower()
                 or text in (p["team"] or "").lower()]  # fmt: skip
    pos = one("position").upper()
    if pos in ("GKP", "DEF", "MID", "FWD"):
        items = [p for p in items if p["position"] == pos]
    key = one("sort", "xp")
    if key == "name":
        items = sorted(items, key=lambda p: (p["name"] or "").lower())
    else:
        key = key if key in ("xp", "price", "owned", "points") else "xp"
        known = sorted((p for p in items if p[key] is not None), key=lambda p: -p[key])
        items = known + [p for p in items if p[key] is None]  # unknown last, never as zero
    try:
        limit = int(one("limit", "25"))
        offset = max(0, int(one("offset", "0")))
    except ValueError as exc:
        raise Rejected("limit and offset must be whole numbers") from exc
    limit = limit if limit in PAGE_SIZES else 25
    return {**data, "items": items[offset:offset + limit], "total": len(items),
            "offset": offset, "limit": limit}  # fmt: skip


def _json(code: int, payload: Any) -> tuple[str, bytes, str]:
    reason = {200: "OK", 202: "Accepted", 400: "Bad Request", 403: "Forbidden", 404: "Not Found",
              405: "Method Not Allowed", 409: "Conflict", 421: "Misdirected Request",
              500: "Internal Server Error"}[code]  # fmt: skip
    body = json.dumps(payload, default=str).encode()
    return f"{code} {reason}", body, "application/json; charset=utf-8"


def _body(env: dict) -> dict[str, Any]:
    try:
        n = int(env.get("CONTENT_LENGTH") or 0)
    except ValueError as exc:
        raise Rejected("bad Content-Length") from exc
    if n > MAX_BODY:
        raise Rejected("request body too large")
    raw = env["wsgi.input"].read(n) if n else b""
    try:
        body = json.loads(raw or b"{}")
    except ValueError as exc:
        raise Rejected("body must be JSON") from exc
    if not isinstance(body, dict):
        raise Rejected("body must be a JSON object")
    return body


class _Server(ThreadingMixIn, WSGIServer):
    daemon_threads = True


class _Quiet(WSGIRequestHandler):
    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
        pass


def serve(db_path: Path, port: int, state_dir: Path, allow_model_keys: bool = False,
          ready=None) -> None:  # fmt: skip
    app = App(db_path, state_dir, port, allow_model_keys)
    httpd = make_server(HOST, port, app, server_class=_Server, handler_class=_Quiet)
    app.bind(httpd.server_port)
    print(f"FPL agent dashboard on http://{HOST}:{httpd.server_port}  (Ctrl+C to stop)")
    if ready:
        ready(httpd)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()


def serve_in_thread(app: App) -> tuple[WSGIServer, threading.Thread]:
    """For tests: the app on a free port in a background thread."""
    httpd = make_server(HOST, app.port, app, server_class=_Server, handler_class=_Quiet)
    app.bind(httpd.server_port)
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    return httpd, t
