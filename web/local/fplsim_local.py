"""FPL Sim on your own computer: a dashboard for your team's latest plan.

Run it (or double-click "FPL Sim.cmd") and it opens http://127.0.0.1:8765 in your browser. It reads
the plan your agent publishes on GitHub after every run, and the next deadline from FPL. Nothing
to install beyond Python 3; nothing is sent anywhere, and it never touches your FPL team.
"""

from __future__ import annotations

import json
import os
import sys
import threading
import time
import urllib.request
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

REPO = os.environ.get("FPLSIM_REPO", "OPEXAI-GG/fpl-ai-agent")
PORT = int(os.environ.get("FPLSIM_PORT", "8765"))
HERE = Path(__file__).resolve().parent
UA = {"User-Agent": "fplsim-local/1.0"}
CACHE_SECONDS = 60
_cache: dict = {"at": 0.0, "data": None}


def get(url: str, accept: str = "application/json") -> bytes:
    req = urllib.request.Request(url, headers={**UA, "Accept": accept})
    with urllib.request.urlopen(req, timeout=20) as r:
        return r.read()


def next_deadline() -> dict | None:
    events = json.loads(get("https://fantasy.premierleague.com/api/bootstrap-static/"))["events"]
    ev = next((e for e in events if e.get("is_next")), None)
    return {"gw": ev["id"], "deadline": ev["deadline_time"]} if ev else None


def latest() -> dict:
    out: dict = {"plan": None, "report": None, "updated_at": None, "next": None, "error": None}
    try:
        rel = json.loads(get(f"https://api.github.com/repos/{REPO}/releases/tags/plan"))
        assets = {a["name"]: a for a in rel.get("assets", [])}
        if "latest.json" in assets:
            out["plan"] = json.loads(get(assets["latest.json"]["browser_download_url"], "*/*"))
            out["updated_at"] = assets["latest.json"]["updated_at"]
        if "latest.md" in assets:
            out["report"] = get(assets["latest.md"]["browser_download_url"], "*/*").decode()
    except Exception as exc:  # offline, rate-limited or no plan yet
        out["error"] = f"Could not read the latest plan from GitHub ({type(exc).__name__})."
    try:
        out["next"] = next_deadline()
    except Exception:
        pass
    return out


def cached() -> dict:
    now = time.time()
    if _cache["data"] is None or now - _cache["at"] > CACHE_SECONDS:
        fresh = latest()
        if fresh["plan"] is None and _cache["data"] and _cache["data"]["plan"]:
            fresh = {**_cache["data"], "error": fresh["error"]}  # keep the last good plan
        _cache.update(at=now, data=fresh)
    return _cache["data"]


class Handler(BaseHTTPRequestHandler):
    def _send(self, body: bytes, kind: str, status: int = 200) -> None:
        self.send_response(status)
        self.send_header("Content-Type", kind)
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802
        path = self.path.split("?")[0]
        if path in ("/", "/index.html"):
            self._send((HERE / "dashboard.html").read_bytes(), "text/html; charset=utf-8")
        elif path == "/api/plan":
            self._send(json.dumps(cached()).encode(), "application/json")
        else:
            self._send(b"not found", "text/plain", 404)

    def log_message(self, *args) -> None:  # keep the window quiet
        pass


def main() -> int:
    try:
        server = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    except OSError:  # already running: just open it
        webbrowser.open(f"http://127.0.0.1:{PORT}")
        return 0
    url = f"http://127.0.0.1:{PORT}"
    print(f"FPL Sim is running at {url}. Leave this window open; close it to stop.")
    if "--no-browser" not in sys.argv:
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
