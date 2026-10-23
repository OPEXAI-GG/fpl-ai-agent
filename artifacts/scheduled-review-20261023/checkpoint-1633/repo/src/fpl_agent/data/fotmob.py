"""FotMob client (free, unofficial JSON). Personal use, low volume: one league page per
competition per run and one match page per newly finished match, with a politeness gap.

The endpoints live under /api/data/ and can change without notice; every response is checked
against a contract before it is stored, and a change is reported as a skipped source.
"""

from __future__ import annotations

import time
from typing import Any

import httpx

from fpl_agent.errors import SourceFormatError, SourceUnavailable

BASE = "https://www.fotmob.com/api/data"
HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; fpl-agent/0.1)", "Accept": "application/json"}


class FotMobClient:
    def __init__(
        self,
        client: httpx.Client | None = None,
        retries: int = 3,
        backoff: float = 1.0,
        delay: float = 0.4,
    ) -> None:
        self._http = client or httpx.Client(timeout=30, headers=HEADERS, follow_redirects=True)
        self._retries = retries
        self._backoff = backoff
        self._delay = delay

    def _get(self, path: str, params: dict[str, Any]) -> Any:
        url = f"{BASE}/{path}"
        last: Exception | None = None
        for attempt in range(self._retries + 1):
            try:
                resp = self._http.get(url, params=params)
                if resp.status_code in (429, 500, 502, 503, 504):
                    raise httpx.HTTPStatusError("retryable", request=resp.request, response=resp)
                resp.raise_for_status()
                time.sleep(self._delay)
                try:
                    return resp.json()
                except ValueError as exc:  # an HTML page means the endpoint moved
                    raise SourceFormatError(f"GET {url}: response is not JSON") from exc
            except (httpx.TransportError, httpx.HTTPStatusError) as exc:
                last = exc
                if isinstance(exc, httpx.HTTPStatusError) and exc.response.status_code < 429:
                    raise SourceUnavailable(f"GET {url}: HTTP {exc.response.status_code}") from exc
                if attempt < self._retries:
                    time.sleep(self._backoff * 2**attempt)
        raise SourceUnavailable(f"GET {url} failed after {self._retries + 1} attempts") from last

    def league(self, league_id: int, season: str | None = None) -> dict[str, Any]:
        """Fixtures, results, table and season list for one competition ("2025/2026")."""
        params: dict[str, Any] = {"id": league_id, "ccode3": "GBR"}
        if season:
            params["season"] = season
        return self._get("leagues", params)

    def match(self, match_id: int | str) -> dict[str, Any]:
        """Lineups, per-player stats, shot map with xG, team stats for one match."""
        return self._get("matchDetails", {"matchId": match_id})

    def matches_on(self, day: str) -> dict[str, Any]:
        """All matches on a day (YYYYMMDD) across competitions."""
        return self._get("matches", {"date": day})

    def all_leagues(self) -> dict[str, Any]:
        return self._get("allLeagues", {})
