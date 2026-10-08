"""Fake external services backed by the recorded real responses in tests/fixtures/."""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path

import httpx

FIX = Path(__file__).parent / "fixtures"


def fixture(name: str):
    return json.loads((FIX / name).read_text())


def live_payload(bootstrap: dict) -> dict:
    """A consistent live feed: every player 90 minutes, points computed by the rules engine."""
    from fpl_agent.data.players import POSITIONS
    from fpl_agent.rules import Rules, score

    rules = Rules.load(2026)
    els = []
    for e in bootstrap["elements"]:
        stats = {"minutes": 90, "goals_scored": 0, "assists": 0, "clean_sheets": 1,
                 "goals_conceded": 0, "own_goals": 0, "penalties_saved": 0, "penalties_missed": 0,
                 "yellow_cards": 0, "red_cards": 0, "saves": 0, "bonus": 0, "bps": 20,
                 "defensive_contribution": 0}  # fmt: skip
        total = score(stats, POSITIONS[e["element_type"]], rules)["total"]
        stats["total_points"] = total
        els.append({"id": e["id"], "stats": stats,
                    "explain": [{"fixture": 1, "stats": [{"identifier": "minutes", "points": 2,
                                                         "value": 90}]}]})  # fmt: skip
    return {"elements": els}


Response = tuple[int, object]  # (status, json-able body or raw str/bytes)


def make_http(overrides: dict[str, Response | Callable[[], Response]] | None = None):
    """httpx.Client serving the fixtures. `overrides` maps a URL substring to a response (or a
    callable returning one) to inject outages and format changes."""
    overrides = overrides or {}
    boot = fixture("bootstrap-static.json")

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        for needle, resp in overrides.items():
            if needle in url:
                status, body = resp() if callable(resp) else resp
                if isinstance(body, (str, bytes)):
                    return httpx.Response(status, content=body)
                return httpx.Response(status, json=body)
        if "bootstrap-static" in url:
            return httpx.Response(200, json=boot)
        if "/fixtures/" in url:
            return httpx.Response(200, json=fixture("fixtures.json"))
        if "/live/" in url:
            return httpx.Response(200, json=live_payload(boot))
        if "getLeagueData" in url:
            return httpx.Response(200, json=fixture("understat_league.json"))
        if "getPlayerData" in url:
            return httpx.Response(200, json=fixture("understat_player.json"))
        if "mmz4281" in url and url.endswith("E0.csv"):
            return httpx.Response(200, text=(FIX / "fd_E0_2026.csv").read_text())
        if "mmz4281" in url and url.endswith("E1.csv"):
            return httpx.Response(200, text=(FIX / "fd_E1_2026.csv").read_text())
        return httpx.Response(404)

    return httpx.Client(transport=httpx.MockTransport(handler))


def clients(overrides=None):
    from fpl_agent.data.fpl_client import FPLClient
    from fpl_agent.data.understat import UnderstatClient

    http = make_http(overrides)
    return (
        FPLClient(client=http, backoff=0, retries=1),
        UnderstatClient(client=http, backoff=0, retries=1, delay=0),
        http,
    )
