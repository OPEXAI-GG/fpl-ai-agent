"""`fpl-agent team`: corrections and screen reads through the real CLI, network recorded."""

import json

from fpl_agent import cli
from tests.helpers import clients, fixture

BOOT = fixture("bootstrap-static.json")
SQUAD = [e["id"] for e in BOOT["elements"][:15]]
PICKS = {"picks": [{"element": e, "position": i + 1} for i, e in enumerate(SQUAD)],
         "entry_history": {"bank": 3, "value": 1000}}  # fmt: skip
HISTORY = {"current": [{"event": g, "event_transfers": 1} for g in range(1, 6)], "chips": []}


def _net(monkeypatch):
    fpl, _, _ = clients({"/event/5/picks/": (200, PICKS), "/transfers/": (200, []),
                         "/history/": (200, HISTORY)})  # fmt: skip
    monkeypatch.setattr(cli, "FPLClient", lambda: fpl)


def test_a_correction_is_saved_and_confirms_the_count(monkeypatch, tmp_path, capsys):
    _net(monkeypatch)
    code = cli.main(["team", "correct", "--entry", "7", "--team-dir", str(tmp_path),
                     "--set", "free_transfers=2", "--set", "bank=0.5"])  # fmt: skip
    out = capsys.readouterr().out
    assert code == 0 and "saved" in out
    assert "free transfers: 2, confirmed (correction" in out and "bank: £0.5m" in out
    assert list((tmp_path / "7" / "observations").glob("*.json"))


def test_show_lists_only_the_open_question(monkeypatch, tmp_path, capsys):
    _net(monkeypatch)
    assert cli.main(["team", "show", "--entry", "7", "--team-dir", str(tmp_path)]) == 0
    out = capsys.readouterr().out
    assert "free transfers: 1, inferred" in out
    assert "open: your free-transfer count" in out and "confirmed 15" not in out


def test_a_screen_read_is_checked_without_saving(monkeypatch, tmp_path, capsys):
    _net(monkeypatch)
    pos = {1: "GKP", 2: "DEF", 3: "MID", 4: "FWD"}
    els = {e["id"]: e for e in BOOT["elements"]}
    read = {"kind": "screenshot", "screen": "transfers", "entry": 7, "gameweek": 6,
            "observed_at": "2026-09-01T10:00:00Z", "free_transfers": 1, "bank": "£0.3m",
            "players": [{"name": els[e]["web_name"], "position": pos[els[e]["element_type"]]}
                        for e in SQUAD]}  # fmt: skip
    path = tmp_path / "read.json"
    path.write_text(json.dumps(read))
    cli.main(["team", "add", "--check", "--file", str(path), "--team-dir", str(tmp_path)])
    out = capsys.readouterr().out
    assert "checked only: nothing saved" in out and f"(id {SQUAD[0]})" in out
    assert not (tmp_path / "7").exists()


def test_invalid_input_is_rejected(monkeypatch, tmp_path, capsys):
    _net(monkeypatch)
    code = cli.main(["team", "correct", "--entry", "7", "--team-dir", str(tmp_path),
                     "--set", "free_transfers=9"])  # fmt: skip
    assert code == 2 and "rejected" in capsys.readouterr().out
    code = cli.main(["team", "correct", "--entry", "7", "--team-dir", str(tmp_path),
                     "--set", "captain=Haaland"])  # fmt: skip
    assert code == 2 and "cannot correct" in capsys.readouterr().out
