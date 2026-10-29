import json
from pathlib import Path

import httpx
import pytest

from fpl_agent.data.id_map import build_id_map, coverage, normalise, team_key
from fpl_agent.data.understat import UnderstatClient
from fpl_agent.errors import SourceUnavailable

FIX = Path(__file__).parent / "fixtures"


def test_normalise():
    assert normalise("Gabriel Magalhães") == "gabriel magalhaes"
    assert normalise("Dara O&#039;Shea") == "dara o shea"
    assert normalise("Gourna-Douath") == "gourna douath"


def test_team_key_uses_latest_club():
    assert team_key("Everton,Manchester City") == "man city"
    assert team_key("Tottenham") == "spurs"


def test_real_world_coverage():
    fpl = json.loads((FIX / "idmap_fpl.json").read_text(encoding="utf-8"))
    us = json.loads((FIX / "idmap_understat.json").read_text(encoding="utf-8"))
    m = build_id_map(fpl, us)
    cov = coverage(m, fpl)
    assert cov["coverage"] >= 0.99, cov["missing"]
    assert m["fpl_id"].dropna().is_unique  # no FPL player claimed twice
    assert m["understat_id"].is_unique


def test_ambiguous_names_are_not_guessed():
    fpl = {
        "teams": [{"id": 1, "name": "Alpha", "short_name": "ALP"}],
        "elements": [
            {"id": 1, "first_name": "A", "second_name": "Thomas", "web_name": "Thomas",
             "team": 1, "minutes": 100},
            {"id": 2, "first_name": "B", "second_name": "Thomas", "web_name": "Thomas",
             "team": 1, "minutes": 100},
        ],
    }  # fmt: skip
    us = [{"id": "9", "player_name": "Thomas", "team_title": "Alpha", "time": "100"}]
    m = build_id_map(fpl, us, overrides={})
    assert m.loc[0, "method"] == "unmatched"


def test_override_wins():
    fpl = json.loads((FIX / "idmap_fpl.json").read_text(encoding="utf-8"))
    us = json.loads((FIX / "idmap_understat.json").read_text(encoding="utf-8"))
    some = us[0]
    m = build_id_map(fpl, us, overrides={int(some["id"]): 1})
    row = m[m.understat_id == int(some["id"])].iloc[0]
    assert row.fpl_id == 1 and row.method == "override"


def test_client_parses_and_sends_ajax_headers():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["h"] = dict(request.headers)
        seen["path"] = request.url.path
        return httpx.Response(
            200, json=json.loads((FIX / "understat_league.json").read_text(encoding="utf-8"))
        )

    c = UnderstatClient(client=httpx.Client(transport=httpx.MockTransport(handler)), delay=0)
    data = c.league_data("2026")
    assert seen["path"] == "/getLeagueData/EPL/2026"
    assert seen["h"]["x-requested-with"] == "XMLHttpRequest"
    assert len(data["players"]) == 5


def test_client_raises_on_404():
    c = UnderstatClient(
        client=httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(404))),
        delay=0,
        backoff=0,
    )
    with pytest.raises(SourceUnavailable):
        c.player_data(1)


def test_player_frames_numeric_and_shot_context():
    from fpl_agent.data.understat import player_frames

    data = {
        "player": {"id": "7", "name": "X"},
        "matches": [{"goals": "1", "xG": "0.4", "date": "2026-09-20", "time": "90"}],
        "shots": [{"xG": "0.3", "situation": "SetPiece", "shotType": "Head", "result": "Goal"}],
    }
    matches, shots = player_frames(data)
    assert matches.loc[0, "xG"] == 0.4 and matches.loc[0, "understat_id"] == 7
    assert shots.loc[0, "situation"] == "SetPiece"
