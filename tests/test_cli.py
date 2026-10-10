"""Smoke tests of the real CLI entry points, with the network replaced by recorded responses."""

import subprocess
import sys

import duckdb
import pytest

from fpl_agent import cli, notify, pipeline
from tests.helpers import clients


@pytest.fixture
def fake_net(monkeypatch, tmp_path):
    def install(overrides=None):
        fpl, us, http = clients(overrides)
        monkeypatch.setattr(pipeline, "FPLClient", lambda: fpl)
        monkeypatch.setattr(pipeline, "UnderstatClient", lambda: us)
        monkeypatch.setattr(pipeline, "http_client", lambda: http)
        monkeypatch.setattr(cli, "FPLClient", lambda: fpl)
        monkeypatch.setattr(cli, "UnderstatClient", lambda: us)

    monkeypatch.setenv("SNAPSHOT_DIR", str(tmp_path / "snaps"))
    sent = []
    monkeypatch.setattr(notify, "send", lambda m, *a, **k: sent.append(m) or True)
    install()
    install.sent = sent
    return install


def test_help_runs_as_installed_command():
    out = subprocess.run(
        [sys.executable, "-m", "fpl_agent.cli", "--help"], capture_output=True, text=True
    )
    assert out.returncode == 0 and "db-update" in out.stdout


def test_db_update_end_to_end(fake_net, tmp_path, capsys):
    db_path = tmp_path / "f.duckdb"
    code = cli.main(
        [
            "db-update",
            "--db",
            str(db_path),
            "--points",
            "--players",
            "--export",
            str(tmp_path / "pq"),
        ]
    )
    assert code == 0
    out = capsys.readouterr().out
    assert "fpl:bootstrap: ok" in out and "quality: no issues" in out
    con = duckdb.connect(str(db_path))
    assert con.execute("SELECT count(*) FROM players").fetchone()[0] > 0
    assert (tmp_path / "pq" / "players.parquet").exists()


def test_db_update_exit_code_when_fpl_down(fake_net, tmp_path, capsys):
    fake_net({"bootstrap-static": (503, "")})
    code = cli.main(["db-update", "--db", str(tmp_path / "f.duckdb")])
    assert code == 2
    assert "FAILED (critical)" in capsys.readouterr().out
    assert fake_net.sent  # alert sent


def test_snapshot_command(fake_net, tmp_path, capsys):
    assert cli.main(["snapshot"]) == 0
    assert "snapshot saved" in capsys.readouterr().out
    assert list((tmp_path / "snaps").rglob("*.json"))


def test_understat_command(fake_net, capsys):
    assert cli.main(["understat"]) == 0
    assert "coverage" in capsys.readouterr().out


def test_review_on_an_empty_database_reports_unavailable(tmp_path, capsys):
    assert (
        cli.main(["review", "--db", str(tmp_path / "e.duckdb"), "--season", "2026", "--gw", "3"])
        == 0
    )
    out = capsys.readouterr().out
    assert '"available": false' in out


def test_backtest_without_history_says_so(tmp_path, capsys):
    code = cli.main(["backtest", "--db", str(tmp_path / "e.duckdb"), "--season", "2025"])
    assert code == 2 and "not enough history" in capsys.readouterr().out


def test_replay_without_history_says_so(tmp_path, capsys):
    code = cli.main(["replay", "--db", str(tmp_path / "e.duckdb"), "--season", "2025"])
    assert code == 2 and "no gameweeks could be replayed" in capsys.readouterr().out


def test_plan_reports_an_fpl_outage_with_exit_code_2(tmp_path, capsys, monkeypatch):
    from fpl_agent import agent

    class Down:
        def bootstrap(self):
            raise RuntimeError("down")

    monkeypatch.setattr(agent, "FPLClient", lambda: Down())
    monkeypatch.delenv("AGENT_MODE", raising=False)
    assert cli.main(["plan", "--db", str(tmp_path / "e.duckdb"), "--entry", "1"]) == 2
    assert "DRY mode" in capsys.readouterr().out


def test_review_can_export_the_database(tmp_path, capsys):
    out = tmp_path / "pq"
    cli.main(["review", "--db", str(tmp_path / "e.duckdb"), "--export", str(out)])
    capsys.readouterr()
    assert out.exists()


def test_status_reports_dry_by_default(monkeypatch, capsys):
    monkeypatch.delenv("AGENT_MODE", raising=False)
    assert cli.main(["status"]) == 0
    assert "Mode: DRY" in capsys.readouterr().out


def test_status_live_without_credentials_stops(monkeypatch, capsys):
    monkeypatch.setenv("AGENT_MODE", "live")
    for k in ("FPL_REFRESH_TOKEN", "FPL_TOKEN_URL", "FPL_CLIENT_ID", "FPL_ENTRY_ID"):
        monkeypatch.delenv(k, raising=False)
    assert cli.main(["status"]) == 2
    out = capsys.readouterr().out
    assert "LIVE" in out and "FPL_REFRESH_TOKEN" in out


def test_status_live_with_credentials_and_the_kill_switch(monkeypatch, capsys):
    monkeypatch.setenv("AGENT_MODE", "live")
    for k in ("FPL_REFRESH_TOKEN", "FPL_TOKEN_URL", "FPL_CLIENT_ID", "FPL_ENTRY_ID"):
        monkeypatch.setenv(k, "secret-value")
    assert cli.main(["status"]) == 0
    assert "secret-value" not in capsys.readouterr().out
    monkeypatch.setenv("AGENT_ENABLED", "false")
    assert cli.main(["status"]) == 0
    assert "Kill switch is ON" in capsys.readouterr().out


def test_explain_reports_a_missing_plan(tmp_path, capsys):
    db = tmp_path / "f.duckdb"
    assert cli.main(["explain", "--db", str(db), "--gw", "6", "--players", "Mbeumo"]) == 1
    assert "no stored projection" in capsys.readouterr().out


def test_token_check_alerts_when_fpl_refuses_the_token(monkeypatch, capsys):
    from fpl_agent.execution import auth

    monkeypatch.setenv("AGENT_MODE", "live")
    monkeypatch.setenv("FPL_ENTRY_ID", "123")
    sent = []
    monkeypatch.setattr(notify, "send", lambda m, *a, **k: sent.append(m) or True)

    class Dead:
        def exchange(self):
            raise auth.AuthError("FPL sign-in failed (HTTP 400 invalid_grant)")

    monkeypatch.setattr(auth.TokenExchange, "from_env", classmethod(lambda cls: Dead()))
    assert cli.main(["token-check"]) == 2
    assert sent and "FPL_REFRESH_TOKEN" in sent[0] and "invalid_grant" in sent[0]


def test_token_check_passes_quietly_when_the_team_is_read(monkeypatch, capsys):
    from fpl_agent.execution import auth, fpl_writer

    monkeypatch.setenv("AGENT_MODE", "live")
    monkeypatch.setenv("FPL_ENTRY_ID", "123")
    sent = []
    monkeypatch.setattr(notify, "send", lambda m, *a, **k: sent.append(m) or True)

    class Ok:
        def exchange(self):
            return auth.Tokens("a", "r")

    monkeypatch.setattr(auth.TokenExchange, "from_env", classmethod(lambda cls: Ok()))
    monkeypatch.setattr(fpl_writer.FPLWriter, "my_team", lambda self: {"picks": []})
    assert cli.main(["token-check"]) == 0
    assert not sent and "sign-in OK" in capsys.readouterr().out


def test_token_check_does_nothing_in_dry_mode(monkeypatch, capsys):
    monkeypatch.delenv("AGENT_MODE", raising=False)
    assert cli.main(["token-check"]) == 0
    assert "skipped" in capsys.readouterr().out


def test_gw_summary_prints_and_notifies(monkeypatch, capsys):
    from tests.test_gw_summary import FakeFPL

    monkeypatch.setattr(cli, "FPLClient", FakeFPL)
    sent = []
    monkeypatch.setattr(notify, "send", lambda text: sent.append(text))
    monkeypatch.delenv("FPL_ENTRY_ID", raising=False)
    assert cli.main(["gw-summary"]) == 0 and "skipped" in capsys.readouterr().out
    assert cli.main(["gw-summary", "--entry", "1", "--notify"]) == 0
    assert "GW6: 70 points" in capsys.readouterr().out and sent and "GW6" in sent[0]


def test_explain_without_a_gameweek_uses_the_latest_plan(tmp_path, capsys):
    db = tmp_path / "f.duckdb"
    assert cli.main(["explain", "--db", str(db), "--players", "Mbeumo"]) == 1
    assert "no stored projection for GW0" in capsys.readouterr().out


@pytest.fixture
def posts(monkeypatch):
    sent = []

    def fake(message, client=None, webhook=None):
        sent.append((webhook, message))
        return fake.ok.pop(0) if fake.ok else True

    fake.ok = []
    monkeypatch.setattr(notify, "send", fake)
    monkeypatch.setenv("DISCORD_APP_ID", "123")
    return sent, fake


def test_send_replies_to_the_slash_command(tmp_path, posts):
    sent, _ = posts
    (tmp_path / "r.md").write_text("plan text\n")
    argv = ["send", "--file", str(tmp_path / "r.md"), "--discord-token", "tok", "--header", "Done"]
    assert cli.main(argv) == 0
    assert sent == [("https://discord.com/api/v10/webhooks/123/tok", "Done\n\nplan text")]


def test_send_falls_back_to_the_webhook_only_when_asked(tmp_path, posts):
    sent, fake = posts
    argv = ["send", "--file", str(tmp_path / "missing.md"), "--discord-token", "tok"]
    fake.ok = [False]
    assert cli.main(argv) == 1 and len(sent) == 1  # expired reply, no fallback
    fake.ok = [False, True]
    assert cli.main([*argv, "--fallback"]) == 0
    assert sent[-1] == (None, "(nothing to report)")


def test_send_without_a_token_uses_the_webhook(tmp_path, posts):
    sent, _ = posts
    (tmp_path / "r.md").write_text("x")
    assert cli.main(["send", "--file", str(tmp_path / "r.md")]) == 0
    assert sent == [(None, "x")]
