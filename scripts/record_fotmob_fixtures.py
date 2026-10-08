"""Re-record the trimmed FotMob fixtures used by the tests (needs network; run by hand).

uv run python scripts/record_fotmob_fixtures.py
"""

from __future__ import annotations

import json
from pathlib import Path

from fpl_agent.data.fotmob import FotMobClient

OUT = Path(__file__).parent.parent / "tests" / "fixtures"


def trim_league(raw: dict, keep_finished: int = 6, keep_open: int = 3) -> dict:
    ms = raw["fixtures"]["allMatches"]
    fin = [m for m in ms if m["status"].get("finished")][:keep_finished]
    opn = [m for m in ms if not m["status"].get("started")][:keep_open]
    return {
        "details": {k: raw["details"][k] for k in ("id", "name", "selectedSeason", "country")},
        "allAvailableSeasons": raw.get("allAvailableSeasons", []),
        "fixtures": {"allMatches": fin + opn},
    }


def trim_match(raw: dict) -> dict:
    c = raw["content"]
    return {
        "general": {
            k: raw["general"][k]
            for k in ("matchId", "leagueId", "leagueName", "homeTeam", "awayTeam",
                      "matchTimeUTCDate", "started", "finished")
        },
        "header": {"teams": raw["header"]["teams"], "status": raw["header"]["status"],
                   "events": {}},
        "content": {
            "lineup": c["lineup"],
            "playerStats": c["playerStats"],
            "shotmap": {"shots": c["shotmap"]["shots"]},
            "stats": {"Periods": {"All": c["stats"]["Periods"]["All"]}},
        },
    }  # fmt: skip


def main() -> None:
    fm = FotMobClient()
    for league_id, name, season in (
        (48, "championship", "2026/2027"),
        (47, "premier", "2026/2027"),
    ):
        lg = trim_league(fm.league(league_id, season))
        (OUT / f"fotmob_league_{name}.json").write_text(json.dumps(lg))
        first = lg["fixtures"]["allMatches"][0]["id"]
        m = trim_match(fm.match(first))
        (OUT / f"fotmob_match_{name}.json").write_text(json.dumps(m))
        print(name, first)


if __name__ == "__main__":
    main()
