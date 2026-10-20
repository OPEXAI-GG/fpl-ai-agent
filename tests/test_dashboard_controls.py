"""The dashboard's controls: only fixed dry actions, one writer at a time, every POST checked for
origin and token, and a job can never sign in to FPL whatever the server inherited."""

import json
import sys
import time

import pytest

from fpl_agent.dashboard.jobs import Busy, Jobs, Rejected, validate_prefs
from fpl_agent.dashboard.server import App, serve_in_thread
from tests.test_dashboard import PORT, call


class ScriptJobs(Jobs):
    """Jobs whose actions run a small Python snippet instead of the CLI."""

    scripts = {
        "refresh": "import time; print('refreshing', flush=True); time.sleep(float(__import__('os').environ.get('SLOW', '0')))",
        "plan": "import sys; print('plan failed'); sys.exit(3)",
        "review": "import os, json; print(json.dumps({k: os.environ.get(k) for k in "
                  "('AGENT_MODE', 'FPL_REFRESH_TOKEN', 'NOTIFY_WEBHOOK', 'ANTHROPIC_API_KEY')}))",
    }  # fmt: skip

    def command(self, action, prefs):
        super().command(action, {**prefs, "entry_id": prefs.get("entry_id") or 1})  # validates
        return [self.python, "-c", self.scripts[action], "x", "x", "x"]


@pytest.fixture
def jobs(tmp_path):
    return ScriptJobs(tmp_path / "db.duckdb", tmp_path / "state")


def post(app, path, body=None, origin=None, token=None, host=None):
    host = host or f"127.0.0.1:{app.port}"
    headers = {"x_dashboard_token": app.token if token is None else token}
    if origin != "":
        headers["origin"] = origin or f"http://{host}"
    return call(app, path, "POST", body if body is not None else {}, host=host, **headers)


def test_actions_are_fixed_argument_lists_with_no_shell(tmp_path):
    j = Jobs(tmp_path / "f.duckdb", tmp_path / "s")
    prefs = {"season": 2026, "horizon": 4, "samples": 200, "entry_id": 123}
    plan = j.command("plan", prefs)
    assert plan[:3] == [sys.executable, "-m", "fpl_agent.cli"] and "--force" in plan
    assert "--notify" not in plan and "--entry" in plan and "123" in plan
    assert j.command("refresh", prefs)[3] == "db-update"
    refresh = j.command("refresh", prefs)
    assert refresh[refresh.index("--fotmob") + 1] == "ENG1,UCL,UEL,UECL"
    assert refresh[refresh.index("--fotmob-matches") + 1] == "40"
    for bad in ("live", "token-check", "rm -rf /", "plan; ls", ""):
        with pytest.raises(Rejected):
            j.command(bad, prefs)
    with pytest.raises(Rejected, match="team id"):
        j.command("plan", {**prefs, "entry_id": None})


def test_jobs_are_forced_dry_and_never_get_credentials(tmp_path, monkeypatch):
    for k, v in {"AGENT_MODE": "live", "FPL_REFRESH_TOKEN": "rt", "FPL_CLIENT_ID": "c",
                 "FPL_TOKEN_URL": "u", "NOTIFY_WEBHOOK": "w", "GH_TOKEN": "g",
                 "ANTHROPIC_API_KEY": "k", "FPL_ENTRY_ID": "9"}.items():  # fmt: skip
        monkeypatch.setenv(k, v)
    env = Jobs(tmp_path / "f.duckdb", tmp_path / "s").env()
    assert env["AGENT_MODE"] == "dry" and env["AGENT_ENABLED"] == "true"
    for k in ("FPL_REFRESH_TOKEN", "FPL_CLIENT_ID", "FPL_TOKEN_URL", "NOTIFY_WEBHOOK",
              "GH_TOKEN", "ANTHROPIC_API_KEY", "FPL_ENTRY_ID"):  # fmt: skip
        assert k not in env, k
    assert Jobs(tmp_path / "f.duckdb", tmp_path / "s2", allow_model_keys=True).env()[
        "ANTHROPIC_API_KEY"] == "k"  # fmt: skip


def test_a_live_mode_server_still_runs_the_real_cli_dry(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENT_MODE", "live")
    monkeypatch.setenv("FPL_REFRESH_TOKEN", "would-sign-in")

    class StatusJobs(Jobs):
        def command(self, action, prefs):  # the real CLI, its read-only status command
            return [self.python, "-m", "fpl_agent.cli", "status"]

    j = StatusJobs(tmp_path / "f.duckdb", tmp_path / "s")
    job = j.wait(j.start("refresh").id, timeout=60)
    assert job.status == "succeeded" and "Mode: DRY" in job.output
    assert "would-sign-in" not in job.output


def test_child_environment_seen_by_the_job(jobs, monkeypatch):
    monkeypatch.setenv("AGENT_MODE", "live")
    monkeypatch.setenv("FPL_REFRESH_TOKEN", "rt")
    job = jobs.wait(jobs.start("review").id)
    seen = json.loads(job.output.strip().splitlines()[-1])
    assert seen == {"AGENT_MODE": "dry", "FPL_REFRESH_TOKEN": None, "NOTIFY_WEBHOOK": None,
                    "ANTHROPIC_API_KEY": None}  # fmt: skip


def test_two_writers_one_accepted_one_busy_and_server_stays_responsive(tmp_path, monkeypatch):
    monkeypatch.setenv("SLOW", "3")
    app = App(tmp_path / "db.duckdb", tmp_path / "state", 0,
              jobs=ScriptJobs(tmp_path / "db.duckdb", tmp_path / "state"))  # fmt: skip
    httpd, _ = serve_in_thread(app)
    try:
        code, first, _ = post(app, "/api/jobs", {"action": "refresh"})
        assert code == 202
        code, second, _ = post(app, "/api/jobs", {"action": "review"})
        assert code == 409 and "running" in second["error"]
        import urllib.request

        t = time.perf_counter()
        with urllib.request.urlopen(f"http://127.0.0.1:{app.port}/api/overview", timeout=5) as r:
            assert r.status == 200 and json.loads(r.read())["meta"]["refresh_in_progress"]
        assert time.perf_counter() - t < 2  # answered while the job still runs
        assert app.jobs.wait(first["id"], timeout=30).status == "succeeded"
        assert not app.jobs.lock_file.exists()
    finally:
        httpd.shutdown()
        httpd.server_close()


def test_a_second_dashboard_on_the_same_state_is_refused(tmp_path, monkeypatch):
    monkeypatch.setenv("SLOW", "2")
    a = ScriptJobs(tmp_path / "db.duckdb", tmp_path / "state")
    job = a.start("refresh")
    b = ScriptJobs(tmp_path / "db.duckdb", tmp_path / "state")
    with pytest.raises(Busy):
        b.start("refresh")
    a.wait(job.id)


def test_post_requires_same_origin_token_and_valid_input(tmp_path, jobs):
    app = App(tmp_path / "db.duckdb", tmp_path / "state", PORT, jobs=jobs)
    assert post(app, "/api/jobs", {"action": "refresh"}, origin="http://evil.example")[0] == 403
    assert post(app, "/api/jobs", {"action": "refresh"}, origin="")[0] == 403
    assert post(app, "/api/jobs", {"action": "refresh"}, token="")[0] == 403
    assert post(app, "/api/jobs", {"action": "refresh"}, token="guess")[0] == 403
    assert post(app, "/api/jobs", {"action": "live-transfer"})[0] == 400
    assert post(app, "/api/jobs", {"action": ["refresh"]})[0] == 400
    assert call(app, "/api/jobs", host="evil.example", method="POST", body={})[0] == 421
    big = {"action": "refresh", "pad": "x" * 5000}
    assert post(app, "/api/jobs", big)[0] == 400
    for prefs in ({"season": 1900}, {"horizon": 99}, {"samples": "1e9"}, {"entry_id": "12; rm"},
                  {"entry_id": True}, {"db": "/etc/passwd"}, {"out": "../x"}):  # fmt: skip
        assert post(app, "/api/preferences", prefs)[0] == 400, prefs
    assert not any(call(app, "/api/jobs")[1]["items"])  # nothing ran


def test_get_never_changes_anything(tmp_path, jobs):
    app = App(tmp_path / "db.duckdb", tmp_path / "state", PORT, jobs=jobs)
    assert call(app, "/api/jobs?action=refresh")[1]["items"] == []
    assert call(app, "/api/jobs/x/cancel")[0] == 404
    assert call(app, "/api/preferences?season=1999")[1]["season"] == 2026


def test_preferences_are_validated_and_stored_without_secrets(tmp_path, jobs):
    app = App(tmp_path / "db.duckdb", tmp_path / "state", PORT, jobs=jobs)
    code, saved, _ = post(app, "/api/preferences", {"entry_id": "123", "season": "2026",
                                                     "horizon": "4", "samples": "300"})  # fmt: skip
    assert code == 200 and saved == {"season": 2026, "horizon": 4, "samples": 300, "entry_id": 123}
    stored = json.loads(jobs.prefs_file.read_text(encoding="utf-8"))
    assert stored == saved and call(app, "/api/preferences")[1] == saved
    assert validate_prefs({"entry_id": ""})["entry_id"] is None


def test_failed_job_and_cancel_are_visible(jobs, monkeypatch):
    failed = jobs.wait(jobs.start("plan").id)
    assert failed.status == "failed" and failed.exit_code == 3 and "plan failed" in failed.output
    monkeypatch.setenv("SLOW", "20")
    job = jobs.start("refresh")
    deadline = time.monotonic() + 10
    while jobs.get(job.id).status != "running" and time.monotonic() < deadline:
        time.sleep(0.05)
    jobs.cancel(job.id)
    done = jobs.wait(job.id, timeout=15)
    assert done.status == "cancelled" and not jobs.lock_file.exists()


def test_restart_marks_an_unfinished_job_interrupted(tmp_path):
    state = tmp_path / "state"
    state.mkdir()
    (state / "jobs.json").write_text(json.dumps([{"id": "abc", "action": "plan", "args": [],
        "status": "running", "created_at": "2026-10-20T10:00:00Z"}]), encoding="utf-8")  # fmt: skip
    (state / "writer.lock").write_text("123", encoding="utf-8")
    j = Jobs(tmp_path / "db.duckdb", state)
    assert j.get("abc").status == "interrupted" and not j.busy()
    assert not (state / "writer.lock").exists()  # the dead dashboard's lock is released
