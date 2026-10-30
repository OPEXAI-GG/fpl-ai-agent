"""The Odds API (https://the-odds-api.com): bookmaker 1X2 and totals for upcoming matches.

Optional. Without `ODDS_API_KEY` nothing is fetched and the team model runs on results and xG
alone; with it, the market's goal rates are blended in (see `model/odds.py`). The free tier is
500 requests a month; one request per run covers every match, so it is far below that.
"""

from __future__ import annotations

import os
from datetime import datetime
from typing import Any

import httpx
import pandas as pd

from fpl_agent.data.leagues import get as get_league
from fpl_agent.db.database import FootballDB, now, team_key
from fpl_agent.errors import SourceFormatError, SourceUnavailable

BASE = "https://api.the-odds-api.com/v4/sports"
SPORT_KEYS = {"ENG1": "soccer_epl", "ENG2": "soccer_efl_champ"}
SOURCE = "oddsapi"


def api_key() -> str | None:
    return os.environ.get("ODDS_API_KEY") or None


class OddsClient:
    def __init__(self, key: str | None = None, client: httpx.Client | None = None) -> None:
        self.key = key or api_key()
        self._http = client or httpx.Client(timeout=30)

    @property
    def enabled(self) -> bool:
        return bool(self.key)

    def upcoming(self, sport: str = "soccer_epl") -> list[dict[str, Any]]:
        if not self.key:
            raise SourceUnavailable("ODDS_API_KEY is not set")
        try:
            r = self._http.get(f"{BASE}/{sport}/odds", params={
                "apiKey": self.key, "regions": "uk,eu", "markets": "h2h,totals",
                "oddsFormat": "decimal"})  # fmt: skip
            r.raise_for_status()
            data = r.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise SourceUnavailable(f"odds API failed: {type(exc).__name__}") from exc
        if not isinstance(data, list):
            raise SourceFormatError("odds API: expected a list of events")
        return data


def _consensus(event: dict[str, Any]) -> dict[str, float] | None:
    """Average decimal prices across bookmakers (1X2 and the 2.5 total)."""
    home, away = event["home_team"], event["away_team"]
    acc: dict[str, list[float]] = {k: [] for k in ("home", "draw", "away", "over25", "under25")}
    for bk in event.get("bookmakers", []):
        for mk in bk.get("markets", []):
            outs = mk.get("outcomes", [])
            if mk["key"] == "h2h":
                for o in outs:
                    key = "home" if o["name"] == home else "away" if o["name"] == away else "draw"
                    acc[key].append(float(o["price"]))
            elif mk["key"] == "totals":
                for o in outs:
                    if abs(float(o.get("point", 0)) - 2.5) < 1e-9:
                        acc["over25" if o["name"].lower() == "over" else "under25"].append(
                            float(o["price"])
                        )
    if not (acc["home"] and acc["draw"] and acc["away"]):
        return None
    return {k: sum(v) / len(v) if v else None for k, v in acc.items()}


def load_events(db: FootballDB, events: list[dict[str, Any]], comp: str = "ENG1") -> int:
    """Attach consensus odds to the not-yet-played matches they describe. Returns rows written."""
    lg = get_league(comp)
    teams = {
        k: t for t, k in db.con.execute("SELECT team_id, key FROM teams").fetchall()
    }  # key -> id
    rows, stamp = [], now()
    for ev in events:
        cons = _consensus(ev)
        if not cons:
            continue
        h, a = teams.get(team_key(ev["home_team"])), teams.get(team_key(ev["away_team"]))
        if h is None or a is None:
            continue
        ko = datetime.fromisoformat(ev["commence_time"].replace("Z", "+00:00")).replace(tzinfo=None)
        m = db.con.execute(
            "SELECT match_id FROM matches WHERE competition_id = ? AND home_team_id = ? "
            "AND away_team_id = ? AND NOT coalesce(finished, false) "
            "ORDER BY abs(epoch(kickoff) - epoch(?)) LIMIT 1", [lg.comp_id, h, a, ko],
        ).fetchone()  # fmt: skip
        if not m:
            continue
        rows.append({"match_id": m[0], "source": SOURCE, "bookmaker": "consensus",
                     "closing": False, **cons, "ah_line": None, "ah_home": None,
                     "ah_away": None, "ingested_at": stamp})  # fmt: skip
    n = db.upsert("match_odds", pd.DataFrame(rows))
    db.log_odds(pd.DataFrame(rows))
    db.log(SOURCE, "match_odds", n, comp)
    return n
