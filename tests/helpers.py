"""Fake external services backed by the recorded real responses in tests/fixtures/."""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path

import httpx

FIX = Path(__file__).parent / "fixtures"


def fixture(name: str):
    return json.loads((FIX / name).read_text(encoding="utf-8"))


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


def _synth_match(match_id: str) -> dict | None:
    """The recorded match page re-labelled as another fixture from the recorded league pages, so
    update tests can fetch every finished match without hundreds of KB of fixtures."""
    import copy

    for name in ("championship", "premier"):
        for m in fixture(f"fotmob_league_{name}.json")["fixtures"]["allMatches"]:
            if m["id"] == match_id and m["status"].get("finished"):
                page = copy.deepcopy(fixture(f"fotmob_match_{name}.json"))
                hg, ag = (int(x) for x in m["status"]["scoreStr"].split(" - "))
                page["general"].update(
                    matchId=match_id, homeTeam=m["home"], awayTeam=m["away"],
                    matchTimeUTCDate=m["status"]["utcTime"].replace("Z", ".000Z"),
                )  # fmt: skip
                page["header"]["teams"][0]["score"] = hg
                page["header"]["teams"][1]["score"] = ag
                return page
    return None


def _fotmob(request: httpx.Request, all_matches: bool = False) -> httpx.Response:
    q = dict(request.url.params)
    if request.url.path.endswith("/leagues"):
        name = {"48": "championship", "47": "premier"}.get(q.get("id", ""))
        if name:
            return httpx.Response(200, json=fixture(f"fotmob_league_{name}.json"))
    if request.url.path.endswith("/matchDetails"):
        for name, mid in (("championship", "5836754"), ("premier", "5795363")):
            if q.get("matchId") == mid:
                return httpx.Response(200, json=fixture(f"fotmob_match_{name}.json"))
        page = _synth_match(q.get("matchId", "")) if all_matches else None
        if page:
            return httpx.Response(200, json=page)
    return httpx.Response(404)


Response = tuple[int, object]  # (status, json-able body or raw str/bytes)


def make_http(
    overrides: dict[str, Response | Callable[[], Response]] | None = None, all_matches: bool = False
):
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
        if "raw.githubusercontent.com" in url and "vaastav" in url:
            if "players_raw" in url:
                return httpx.Response(
                    200, text=(FIX / "vaastav_2017_players_raw.csv").read_text(encoding="utf-8")
                )
            old = any(f"/{y}-" in url for y in (2016, 2017, 2018, 2019))
            return httpx.Response(
                200,
                text=(FIX / ("vaastav_2017.csv" if old else "vaastav_2024.csv")).read_text(
                    encoding="utf-8"
                ),
            )
        if "fotmob.com" in url:
            return _fotmob(request, all_matches)
        if "getLeagueData" in url:
            return httpx.Response(200, json=fixture("understat_league.json"))
        if "getPlayerData" in url:
            return httpx.Response(200, json=fixture("understat_player.json"))
        if "mmz4281" in url and url.endswith("E0.csv"):
            return httpx.Response(200, text=(FIX / "fd_E0_2026.csv").read_text(encoding="utf-8"))
        if "mmz4281" in url and url.endswith("E1.csv"):
            return httpx.Response(200, text=(FIX / "fd_E1_2026.csv").read_text(encoding="utf-8"))
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


def fotmob_client(overrides=None, all_matches: bool = False):
    from fpl_agent.data.fotmob import FotMobClient

    return FotMobClient(client=make_http(overrides, all_matches), backoff=0, retries=1, delay=0)
