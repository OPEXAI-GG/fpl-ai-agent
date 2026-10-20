"""The local dashboard's read side: it opens the database read-only, shows exactly what is
stored (or says what is missing), and serves nothing but its own JSON and three static files."""

import io
import json
import subprocess
import sys
import time
from datetime import UTC, datetime, timedelta
from importlib import resources

import pandas as pd
import pytest

from fpl_agent import notify
from fpl_agent.dashboard.server import App
from fpl_agent.db.database import FootballDB
from fpl_agent.pipeline import UpdateOptions, run_update
from tests.helpers import clients

PORT = 8765


@pytest.fixture(scope="module")
def seeded(tmp_path_factory):
    """A real database from the recorded FPL fixtures, plus a decision and two projection runs
    with known numbers."""
    path = tmp_path_factory.mktemp("dash") / "football.duckdb"
    db = FootballDB(path)
    fpl, us, http = clients()
    mp = pytest.MonkeyPatch()
    mp.setattr(notify, "send", lambda *a, **k: True)
    run_update(db, UpdateOptions(season=2026, football_data=("E0",), points=True), fpl=fpl,
               us=us, http=http)  # fmt: skip
    mp.undo()
    season, gw = db.con.execute(
        "SELECT season, max(gw) FROM fpl_players_gw GROUP BY season").fetchone()  # fmt: skip
    pids = [r[0] for r in db.con.execute(
        "SELECT player_id FROM players ORDER BY player_id LIMIT 15").fetchall()]  # fmt: skip
    made = datetime.now(UTC).replace(tzinfo=None) - timedelta(minutes=5)

    def run(run_id, at, xp):
        db.append("projections", pd.DataFrame({
            "run_id": run_id, "model": "t", "created_at": at, "season": season, "gw": gw,
            "player_id": pids, "xpts": [xp + i / 10 for i in range(15)],
            "detail": [json.dumps({"q10": 0, "q90": 9, "p_zero": 0.1, "exp_minutes": 80})] * 15,
        }))  # fmt: skip

    run("before", made - timedelta(minutes=1), 3.0)
    run("after", made + timedelta(minutes=1), 7.0)  # a later run must not be linked
    payload = {"gw": gw, "chip": None, "transfers_in": [pids[14]], "transfers_out": [pids[13]],
               "hits": 0, "captain": pids[0], "vice": pids[1], "xi": pids[:11],
               "bench": pids[11:], "xp": 55.5, "xp_no_transfers": 54.0, "bank_after": 15,
               "warnings": ["<script>alert(1)</script>"], "captain_options": [],
               "held": {}, "plan": []}  # fmt: skip
    db.append("decisions", pd.DataFrame([
        {"season": season, "gw": gw - 1, "created_at": made - timedelta(days=7), "mode": "live",
         "payload": json.dumps({**payload, "gw": gw - 1})},
        {"season": season, "gw": gw, "created_at": made, "mode": "dry",
         "payload": json.dumps(payload)},
    ]))  # fmt: skip
    db.close()
    return path, pids, season, gw


def call(app, path, method="GET", body=None, host=f"127.0.0.1:{PORT}", **headers):
    raw = json.dumps(body).encode() if body is not None else b""
    env = {"REQUEST_METHOD": method, "PATH_INFO": path.split("?")[0],
           "QUERY_STRING": path.split("?")[1] if "?" in path else "", "HTTP_HOST": host,
           "CONTENT_LENGTH": str(len(raw)), "wsgi.input": io.BytesIO(raw)}  # fmt: skip
    env.update({f"HTTP_{k.upper()}": v for k, v in headers.items()})
    out = {}

    def start(status, hdrs):
        out["status"], out["headers"] = int(status.split()[0]), dict(hdrs)

    body = b"".join(app(env, start))
    ctype = out["headers"]["Content-Type"]
    return (
        out["status"],
        (json.loads(body) if ctype.startswith("application/json") else body),
        out["headers"],
    )


@pytest.fixture
def app(seeded, tmp_path):
    return App(seeded[0], tmp_path / "state", PORT)


def test_missing_database_renders_setup_guidance(tmp_path):
    a = App(tmp_path / "none.duckdb", tmp_path / "state", PORT)
    for section in ("overview", "team", "recommendations", "players", "planning", "activity"):
        code, body, _ = call(a, f"/api/{section}")
        assert code == 200 and body["meta"]["state"] == "missing" and body["data"] is None
    code, body, _ = call(a, "/api/health")
    assert body["data"]["settings"]["dashboard_jobs"].startswith("always dry")


def test_news_health_preserves_failures_and_recomputes_article_freshness(tmp_path):
    path = tmp_path / "news.duckdb"
    db = FootballDB(path)
    at = datetime.now(UTC).replace(tzinfo=None)
    for i, error in enumerate((None, "HTTPStatusError")):
        payload = {
            "url": f"https://www.mancity.com/news/mens/test-{i}",
            "title": "Fitness update",
            "players": ["Test Player"],
            "source": "official club",
            "published_at": (at - timedelta(days=9)).isoformat(),
            "state": "fresh",
            "error": error,
        }
        db.con.execute(
            "INSERT INTO news_evidence VALUES (?, ?, ?, ?)",
            [str(i), at, payload["url"], json.dumps(payload)],
        )
    db.close()
    app = App(path, tmp_path / "state", PORT)
    code, body, _ = call(app, "/api/health")
    assert code == 200
    assert {r["state"] for r in body["data"]["news"]} == {"failed", "stale"}
    assert not (tmp_path / "none.duckdb").exists()  # never created or migrated


def test_empty_database_says_what_to_do(tmp_path):
    FootballDB(tmp_path / "e.duckdb").close()
    a = App(tmp_path / "e.duckdb", tmp_path / "state", PORT)
    code, body, _ = call(a, "/api/overview")
    assert body["meta"]["state"] == "empty" and "refresh" in body["meta"]["message"]


def test_recommendation_matches_the_stored_decision_exactly(app, seeded):
    _, pids, season, gw = seeded
    code, body, _ = call(app, "/api/recommendations")
    assert code == 200 and body["meta"]["state"] == "ok"
    latest, older = body["data"]["items"]
    assert (latest["gw"], latest["mode"], latest["xp"]) == (gw, "dry", 55.5)
    assert [p["player_id"] for p in latest["xi"]] == pids[:11]
    assert [p["player_id"] for p in latest["bench"]] == pids[11:]
    assert latest["transfers_in"][0]["player_id"] == pids[14]
    assert latest["transfers_out"][0]["player_id"] == pids[13]
    assert latest["projection_run"] == "before"  # not the run saved after the decision
    assert latest["captain"]["xp"] == 3.0 and latest["xi"][4]["xp"] == 3.4
    assert latest["bank_after"] == 1.5 and "nothing sent" in latest["status"]
    assert older["status"].startswith("planned in live mode") and "unknown" in older["entry"]
    assert latest["warnings"] == ["<script>alert(1)</script>"]  # data, rendered as text


def test_overview_team_planning_activity_health(app, seeded):
    _, pids, season, gw = seeded
    ov = call(app, "/api/overview")[1]["data"]
    assert (ov["season"], ov["next_gw"]) == (season, gw)
    assert ov["counts"]["decisions"] == 2 and ov["counts"]["projection_runs"] == 2
    assert ov["latest_decision"]["mode"] == "dry" and ov["snapshot_stale"] is False
    team = call(app, "/api/team")[1]["data"]
    assert team["planned"]["gw"] == gw and len(team["planned"]["xi"]) == 11
    assert team["public_squad"] is None and "sign-in" in team["current_squad_note"]
    plan = call(app, "/api/planning")[1]["data"]
    assert plan["projection"] == [{"gw": gw, "players": 15, "xi_xp": round(
        sum(7.0 + i / 10 for i in range(11)), 2)}]  # fmt: skip
    act = call(app, "/api/activity")[1]["data"]
    assert [d["mode"] for d in act["decisions"]] == ["dry", "live"] and act["ingest"]
    health = call(app, "/api/health")[1]["data"]
    assert health["points_history_through_gw"] and any(
        s["source"] == "fpl" for s in health["sources"])  # fmt: skip


def test_players_search_filter_sort_and_pages(app):
    body = call(app, "/api/players?position=DEF&limit=10&sort=price")[1]["data"]
    assert body["limit"] == 10 and len(body["items"]) <= 10
    assert all(p["position"] == "DEF" for p in body["items"])
    prices = [p["price"] for p in body["items"]]
    assert prices == sorted(prices, reverse=True)
    total = body["total"]
    page2 = call(app, "/api/players?position=DEF&limit=10&sort=price&offset=10")[1]["data"]
    assert page2["total"] == total and {p["player_id"] for p in page2["items"]}.isdisjoint(
        {p["player_id"] for p in body["items"]})  # fmt: skip
    first = body["items"][0]
    found = call(app, f"/api/players?q={first['name'][:5]}")[1]["data"]["items"]
    assert any(p["player_id"] == first["player_id"] for p in found)
    some = [p["player_id"] for p in call(app, "/api/players?limit=10")[1]["data"]["items"]]
    cmp = call(app, f"/api/players?ids={','.join(map(str, some[:6]))}")[1]["data"]
    assert len(cmp["items"]) == 4  # comparisons are capped at four
    xs = [p["xp"] for p in call(app, "/api/players?limit=100")[1]["data"]["items"]]
    known = [x for x in xs if x is not None]
    assert xs[: len(known)] == sorted(known, reverse=True)  # unknown xP last, never as 0


def test_only_its_own_host_and_routes_are_served(app):
    assert call(app, "/api/overview", host="evil.example")[0] == 421
    assert call(app, "/api/overview", host=f"localhost:{PORT}")[0] == 200
    for path in ("/api/sql", "/../pyproject.toml", "/api/overview/../../x", "/static/app.js",
                 "/football.duckdb", "/api/players/1"):  # fmt: skip
        assert call(app, path)[0] == 404, path
    assert call(app, "/api/overview", method="DELETE")[0] == 405


def test_headers_forbid_framing_and_outside_scripts(app):
    code, page, headers = call(app, "/")
    assert code == 200 and app.token.encode() in page and b"__DASHBOARD_TOKEN__" not in page
    assert "default-src 'self'" in headers["Content-Security-Policy"]
    assert headers["X-Frame-Options"] == "DENY" and headers["X-Content-Type-Options"] == "nosniff"


def test_static_assets_use_no_cdn_and_no_html_injection():
    static = resources.files("fpl_agent.dashboard").joinpath("static")
    for name in ("index.html", "app.js", "styles.css"):
        text = static.joinpath(name).read_text(encoding="utf-8")
        assert "http://" not in text and "https://" not in text, name  # no CDN, no network
    js = static.joinpath("app.js").read_text(encoding="utf-8")
    assert "innerHTML" not in js and "insertAdjacentHTML" not in js and "eval(" not in js


def test_player_form_and_history_are_from_stored_rows(app, seeded):
    import duckdb

    ps = call(app, "/api/players?limit=100")[1]["data"]["items"]
    with duckdb.connect(str(seeded[0]), read_only=True) as con:
        for p in ps:
            rows = con.execute(
                "SELECT gw, sum(total_points), sum(minutes) FROM fpl_points_gw "
                "WHERE player_id = ? AND season = ? AND fixture_id <> 0 "
                "GROUP BY gw ORDER BY gw DESC LIMIT 6",
                [p["player_id"], seeded[2]],
            ).fetchall()
            assert p["history"] == [
                {"gw": gw, "points": points, "minutes": minutes} for gw, points, minutes in rows
            ]
            raw = con.execute(
                "SELECT raw FROM fpl_players_gw WHERE player_id = ? "
                "ORDER BY snapshot_at DESC LIMIT 1",
                [p["player_id"]],
            ).fetchone()
            form = json.loads(raw[0]).get("form")
            assert p["form"] == (round(float(form), 2) if form is not None else None)


def test_freshness_advances_without_a_database_write(app):
    call(app, "/api/overview")
    # The cached file version stays identical as time passes.
    old = (datetime.now(UTC) - timedelta(hours=7)).isoformat()
    for name in ("overview", "health"):
        app.model._snap.sections[name]["snapshot_at"] = old
        app.model._snap.sections[name]["snapshot_stale"] = False
        data = call(app, f"/api/{name}")[1]["data"]
        assert data["snapshot_stale"] and data["snapshot_age_hours"] >= 7


def test_execution_history_uses_run_records(seeded, tmp_path, monkeypatch):
    import shutil

    import duckdb

    path = tmp_path / "runs.duckdb"
    shutil.copyfile(seeded[0], path)
    with duckdb.connect(str(path)) as con:
        con.execute(
            "INSERT INTO runs (run_id, season, gw, entry_id, mode, started_at, "
            "ended_at, status, state, exit_code, delivered) "
            "VALUES ('test-run', 2026, 8, 123, 'dry', now(), now(), 'blocked', 'none', 2, false)"
        )
        con.execute(
            "UPDATE runs SET reasons = ?, stages = ?",
            [
                json.dumps(["fixture-token must never reach the page"]),
                json.dumps({"model": {"seconds": 1.25, "status": "ok"}}),
            ],
        )
        con.execute(
            "INSERT INTO scout_calls (called_at, model, gw, outcome, sent, accepted) "
            "VALUES (now(), 'fixture-model', 8, 'parse_error', 2, 0)"
        )
    monkeypatch.setenv("FPL_REFRESH_TOKEN", "fixture-token")
    app = App(path, tmp_path / "state", PORT)
    runs = call(app, "/api/activity")[1]["data"]["runs"]
    assert len(runs) == 1
    assert runs[0]["entry_id"] == 123 and runs[0]["status"] == "blocked"
    assert runs[0]["exit_code"] == 2 and runs[0]["delivered"] is False
    assert "fixture-token" not in json.dumps(call(app, "/api/activity")[1])
    assert runs[0]["stage_seconds"] == {"model": 1.25}
    assert call(app, "/api/overview")[1]["data"]["latest_run"]["run_id"] == "test-run"
    calls = call(app, "/api/activity")[1]["data"]["scout_calls"]
    assert calls[0]["outcome"] == "parse_error" and calls[0]["sent"] == 2


@pytest.mark.parametrize("value", ["false", "0", "off", "no", "invalid"])
def test_dashboard_reports_the_agent_kill_switch_consistently(monkeypatch, value):
    from fpl_agent.dashboard.service import settings_presence

    monkeypatch.setenv("AGENT_ENABLED", value)
    assert settings_presence()["agent_enabled"] is False


def test_no_secret_ever_reaches_a_response(app, monkeypatch):
    monkeypatch.setenv("FPL_REFRESH_TOKEN", "secret-refresh-123")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-secret-456")
    monkeypatch.setenv("NOTIFY_WEBHOOK", "https://hooks.example/secret-789")
    app.model._snap = None
    for section in ("overview", "team", "recommendations", "players", "planning", "activity",
                    "health"):  # fmt: skip
        text = json.dumps(call(app, f"/api/{section}")[1])
        assert "secret" not in text, section
    keys = call(app, "/api/health")[1]["data"]["settings"]["keys"]
    assert keys["ANTHROPIC_API_KEY"] is True and keys["NOTIFY_WEBHOOK"] is True


def test_cached_reads_are_fast_and_the_db_is_closed_between_reads(app, seeded):
    call(app, "/api/overview")  # first load builds the snapshot
    first_ms = app.model._snap.build_ms
    times = []
    for _ in range(40):
        t = time.perf_counter()
        call(app, "/api/players?limit=50")
        times.append(time.perf_counter() - t)
    p95 = sorted(times)[int(0.95 * len(times)) - 1]
    assert p95 < 0.5, (p95, first_ms)
    # nothing holds the file: another process can open it for writing
    code = subprocess.run([sys.executable, "-c", "import duckdb, sys; duckdb.connect(sys.argv[1])"
                           ".close()", str(seeded[0])], capture_output=True).returncode  # fmt: skip
    assert code == 0


def test_a_database_held_by_another_program_keeps_the_last_view(app, seeded):
    call(app, "/api/overview")
    holder = subprocess.Popen(
        [sys.executable, "-c", "import duckdb, sys, time; c = duckdb.connect(sys.argv[1]); "
         "c.execute('CREATE TABLE IF NOT EXISTS t (x INT)'); print('ready', flush=True); "
         "time.sleep(20)", str(seeded[0])],
        stdout=subprocess.PIPE, text=True,
    )  # fmt: skip
    try:
        assert holder.stdout.readline().strip() == "ready"
        app.model._snap.version = ("changed",)  # pretend the file changed under us
        code, body, _ = call(app, "/api/overview")
        assert code == 200 and body["meta"]["db_locked"] is True
        assert body["data"]["season"] is not None  # the last good view
    finally:
        holder.kill()
        holder.wait()
