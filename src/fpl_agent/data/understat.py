"""Understat client (free). Uses the site's own JSON endpoints, which return gzip bodies.

Season is the start year, e.g. "2026" for 2026/27. Understat carries event-level data that
the plan's skill model needs: per-match xG/xA/xGChain/xGBuildup, and per-shot situation,
body part and assist type (so set-piece and penalty threat can be separated from open play).
"""

from __future__ import annotations

import time
from typing import Any

import httpx

BASE = "https://understat.com"


class UnderstatClient:
    def __init__(
        self,
        client: httpx.Client | None = None,
        retries: int = 3,
        backoff: float = 1.0,
        delay: float = 0.5,
    ) -> None:
        self._http = client or httpx.Client(timeout=30, headers={"User-Agent": "fpl-agent/0.1"})
        self._retries = retries
        self._backoff = backoff
        self._delay = delay  # politeness gap between calls

    def _get(self, path: str, referer: str) -> Any:
        url = f"{BASE}/{path.lstrip('/')}"
        headers = {"X-Requested-With": "XMLHttpRequest", "Referer": f"{BASE}/{referer}"}
        last: Exception | None = None
        for attempt in range(self._retries + 1):
            try:
                resp = self._http.get(url, headers=headers)
                if resp.status_code in (429, 500, 502, 503, 504):
                    raise httpx.HTTPStatusError("retryable", request=resp.request, response=resp)
                resp.raise_for_status()
                time.sleep(self._delay)
                return resp.json()  # httpx transparently gunzips
            except (httpx.TransportError, httpx.HTTPStatusError, ValueError) as exc:
                last = exc
                if isinstance(exc, httpx.HTTPStatusError) and exc.response.status_code < 429:
                    raise
                if attempt < self._retries:
                    time.sleep(self._backoff * 2**attempt)
        raise RuntimeError(f"GET {url} failed after {self._retries + 1} attempts") from last

    def league_data(self, season: str, league: str = "EPL") -> dict[str, Any]:
        """teams (with per-match history), players (season totals), dates (fixtures + xG)."""
        return self._get(f"getLeagueData/{league}/{season}", f"league/{league}/{season}")

    def player_data(self, player_id: int | str) -> dict[str, Any]:
        """matches (every season, every league), shots, and season groups for one player."""
        return self._get(f"getPlayerData/{player_id}", f"player/{player_id}")


def player_frames(data: dict[str, Any]):
    """Flatten one player's history into (matches, shots) frames with numeric columns.

    Shots keep situation (OpenPlay/SetPiece/Penalty/...), body part and assist type, which is
    what lets the skill model tell finishing, set-piece and penalty threat apart.
    """
    import pandas as pd

    pid = int(data["player"]["id"])
    out = []
    for key in ("matches", "shots"):
        df = pd.DataFrame(data.get(key, []))
        df.insert(0, "understat_id", pid)
        for col in df.columns:
            if col in ("understat_id",):
                continue
            conv = pd.to_numeric(df[col], errors="coerce")
            if conv.notna().sum() == df[col].notna().sum() and df[col].notna().any():
                df[col] = conv
        out.append(df)
    return out[0], out[1]
