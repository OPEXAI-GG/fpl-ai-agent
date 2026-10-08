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
