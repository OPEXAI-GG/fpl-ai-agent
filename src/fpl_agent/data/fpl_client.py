"""Read-only client for FPL's public endpoints. No auth, no writes."""

from __future__ import annotations

import time
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from typing import Any

import httpx

from fpl_agent.config import FPL_BASE_URL
from fpl_agent.data.schemas import Bootstrap, Fixture
from fpl_agent.errors import SourceFormatError, SourceUnavailable


class FPLClient:
    def __init__(
        self,
        base_url: str = FPL_BASE_URL,
        client: httpx.Client | None = None,
        retries: int = 4,
        backoff: float = 1.0,
    ) -> None:
        self._base = base_url.rstrip("/")
        self._owned = client is None  # close only a connection pool this client created
        self._http = client or httpx.Client(timeout=30, headers={"User-Agent": "fpl-agent/0.1"})
        self._retries = retries
        self._backoff = backoff

    def _get(self, path: str) -> Any:
        url = f"{self._base}/{path.lstrip('/')}"
        last: Exception | None = None
        for attempt in range(self._retries + 1):
            try:
                resp = self._http.get(url)
                if resp.status_code in (429, 500, 502, 503, 504):
                    raise httpx.HTTPStatusError("retryable", request=resp.request, response=resp)
                resp.raise_for_status()
                try:
                    return resp.json()
                except ValueError as exc:  # HTML maintenance page, empty body, ...
                    raise SourceFormatError(f"GET {url}: response is not JSON") from exc
            except (httpx.TransportError, httpx.HTTPStatusError) as exc:
                last = exc
                if isinstance(exc, httpx.HTTPStatusError) and exc.response.status_code < 429:
                    raise SourceUnavailable(f"GET {url}: HTTP {exc.response.status_code}") from exc
                if attempt < self._retries:
                    time.sleep(self._wait(exc, attempt))
        raise SourceUnavailable(f"GET {url} failed after {self._retries + 1} attempts") from last

    MAX_WAIT = 30.0

    def _wait(self, exc: Exception, attempt: int) -> float:
        """Seconds before the next try: the server's Retry-After when given (capped), else
        exponential backoff."""
        if isinstance(exc, httpx.HTTPStatusError):
            header = exc.response.headers.get("Retry-After", "")
            try:
                return min(self.MAX_WAIT, max(0.0, float(header)))
            except ValueError:
                try:
                    until = parsedate_to_datetime(header)
                    return min(self.MAX_WAIT, max(0.0, (until - datetime.now(UTC)).total_seconds()))
                except (ValueError, TypeError, OverflowError):
                    pass
        return min(self.MAX_WAIT, self._backoff * 2**attempt)

    def close(self) -> None:
        if self._owned:
            self._http.close()

    def __enter__(self) -> FPLClient:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def bootstrap_raw(self) -> dict[str, Any]:
        return self._get("bootstrap-static/")

    def bootstrap(self, raw: dict[str, Any] | None = None) -> Bootstrap:
        """The parsed bootstrap; pass `raw` when it was already fetched (one GET per run)."""
        return Bootstrap.model_validate(raw if raw is not None else self.bootstrap_raw())

    def fixtures_raw(self) -> list[dict[str, Any]]:
        return self._get("fixtures/")

    def fixtures(self) -> list[Fixture]:
        return [Fixture.model_validate(f) for f in self.fixtures_raw()]

    def element_summary_raw(self, player_id: int) -> dict[str, Any]:
        return self._get(f"element-summary/{player_id}/")

    def live_raw(self, gw: int) -> dict[str, Any]:
        return self._get(f"event/{gw}/live/")

    def entry_history_raw(self, entry_id: int) -> dict[str, Any]:
        return self._get(f"entry/{entry_id}/history/")

    def entry_picks_raw(self, entry_id: int, gw: int) -> dict[str, Any]:
        return self._get(f"entry/{entry_id}/event/{gw}/picks/")

    def entry_transfers_raw(self, entry_id: int) -> list[dict[str, Any]]:
        return self._get(f"entry/{entry_id}/transfers/")

    def league_standings_raw(self, league_id: int, page: int = 1) -> dict[str, Any]:
        return self._get(f"leagues-classic/{league_id}/standings/?page_standings={page}")
