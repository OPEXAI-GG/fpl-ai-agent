"""The website's only route to FPL: a fixed set of public, read-only endpoints.

* URLs are built here from validated integers; nothing a visitor types is ever requested.
* Every response is cached in SQLite with its retrieval time. A fresh cached copy is served
  without a request; when FPL fails, the last good copy is served and marked stale.
* Outbound requests share a global budget, and a visitor's forced refresh of an entry is
  limited per entry, so nobody can make the service hammer FPL.
* Retries with exponential backoff come from `FPLClient`, with a short timeout.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

from fpl_agent.data.fpl_client import FPLClient
from fpl_agent.errors import SourceFormatError, SourceUnavailable
from fpl_agent.web.store import Store

SOURCE = "FPL public API"
SAMPLE_SOURCE = "sample data"

# seconds a cached response counts as fresh
TTL = {
    "bootstrap": 600,
    "fixtures": 1800,
    "entry": 600,
    "history": 600,
    "picks": 600,
    "picks_final": 6 * 3600,  # a finished gameweek's picks no longer change
    "transfers": 600,
    "standings": 3600,
}
OUTBOUND_PER_MINUTE = 120  # every request the service makes to FPL, all visitors together
REFRESH_EVERY = 300  # a visitor may force fresh data for one entry once per this many seconds


class EntryNotFound(LookupError):
    pass


class ProviderUnavailable(RuntimeError):
    """FPL could not be read and no earlier copy exists (message is user-facing)."""


class RateLimited(RuntimeError):
    pass


@dataclass
class Fetched:
    body: Any
    fetched_at: float  # unix seconds the body was retrieved from FPL
    stale: bool = False  # FPL failed; this is the last good copy
    source: str = SOURCE
    error: str | None = None  # why a stale copy was served

    def meta(self) -> dict[str, Any]:
        return {
            "fetched_at": self.fetched_at,
            "stale": self.stale,
            "source": self.source,
            "age_s": round(max(time.time() - self.fetched_at, 0.0), 1),
            "error": self.error,
        }


class Gateway:
    """Cached public FPL reads. `client` is any object with FPLClient's raw methods."""

    sample = False

    def __init__(
        self,
        store: Store,
        client: FPLClient | None = None,
        outbound_per_minute: int = OUTBOUND_PER_MINUTE,
    ) -> None:
        self.store = store
        self.client = client or FPLClient(
            client=httpx.Client(timeout=12, headers={"User-Agent": "fpl-agent-web/0.1"}),
            retries=2,
            backoff=0.5,
        )
        self.outbound = outbound_per_minute

    # ---- endpoints -------------------------------------------------------------------------
    def bootstrap(self, force: bool = False) -> Fetched:
        return self._get("bootstrap", "bootstrap", self.client.bootstrap_raw, force)

    def fixtures(self) -> Fetched:
        return self._get("fixtures", "fixtures", self.client.fixtures_raw)

    def entry(self, entry_id: int, force: bool = False) -> Fetched:
        return self._get(f"entry:{entry_id}", "entry", lambda: self._entry(entry_id), force)

    def history(self, entry_id: int, force: bool = False) -> Fetched:
        return self._get(
            f"history:{entry_id}", "history", lambda: self.client.entry_history_raw(entry_id), force
        )

    def picks(self, entry_id: int, gw: int, final: bool = False, force: bool = False) -> Fetched:
        return self._get(
            f"picks:{entry_id}:{gw}",
            "picks_final" if final else "picks",
            lambda: self.client.entry_picks_raw(entry_id, gw),
            force,
        )

    def transfers(self, entry_id: int, force: bool = False) -> Fetched:
        return self._get(
            f"transfers:{entry_id}",
            "transfers",
            lambda: self.client.entry_transfers_raw(entry_id),
            force,
        )

    def standings_page(self, page: int) -> Fetched:
        return self._get(
            f"standings:314:{page}",
            "standings",
            lambda: self.client.league_standings_raw(314, page),
        )

    def may_refresh(self, entry_id: int, session_id: str) -> bool:
        """One forced refresh per entry per REFRESH_EVERY seconds (and per session)."""
        return self.store.hit(f"refresh:{entry_id}", 1, REFRESH_EVERY) and self.store.hit(
            f"refresh-session:{session_id}", 3, REFRESH_EVERY
        )

    # ---- machinery -------------------------------------------------------------------------
    def _entry(self, entry_id: int) -> Any:
        return self.client.entry_raw(int(entry_id))

    def _get(self, key: str, ttl_kind: str, fetch, force: bool = False) -> Fetched:
        cached = self.store.cache_get(key)
        now = time.time()
        if cached and not force and now - cached[0] < TTL[ttl_kind]:
            return Fetched(cached[1], cached[0])
        if not self.store.hit("outbound", self.outbound, 60):
            if cached:
                return Fetched(
                    cached[1],
                    cached[0],
                    stale=True,
                    error="request budget reached; showing the last copy",
                )
            raise RateLimited("The service is busy reading FPL. Try again in a minute.")
        try:
            body = fetch()
        except SourceUnavailable as exc:
            if _is_404(exc):
                raise EntryNotFound(key) from exc
            return self._fallback(key, cached, exc)
        except (SourceFormatError, httpx.HTTPError) as exc:
            return self._fallback(key, cached, exc)
        self.store.cache_put(key, body, now)
        return Fetched(body, now)

    @staticmethod
    def _fallback(key: str, cached, exc: Exception) -> Fetched:
        if cached:
            return Fetched(
                cached[1],
                cached[0],
                stale=True,
                error=f"FPL did not respond ({type(exc).__name__})",
            )
        raise ProviderUnavailable(
            "FPL's public data is not responding right now and there is no earlier copy. "
            "Try again in a few minutes."
        ) from exc


def _is_404(exc: Exception) -> bool:
    cause = exc.__cause__
    resp = getattr(cause, "response", None)
    return getattr(resp, "status_code", None) == 404 or "HTTP 404" in str(exc)


class SampleGateway(Gateway):
    """Recorded responses for the labelled sample team: no request leaves the service.

    `root` holds `bootstrap.json`, `fixtures.json` and `entry.json`, `history.json`,
    `picks.json`, `transfers.json` for the fictional sample entry."""

    sample = True

    def __init__(self, root: Path) -> None:  # noqa: D107  (no store, no client)
        self.root = Path(root)
        stamp = self.root / "captured_at.txt"
        self.fetched_at = float(stamp.read_text().strip()) if stamp.exists() else time.time()

    def _file(self, name: str) -> Fetched:
        p = self.root / f"{name}.json"
        if not p.exists():
            raise ProviderUnavailable("The sample data has not been prepared on this server.")
        return Fetched(json.loads(p.read_text()), self.fetched_at, source=SAMPLE_SOURCE)

    def bootstrap(self, force: bool = False) -> Fetched:
        return self._file("bootstrap")

    def fixtures(self) -> Fetched:
        return self._file("fixtures")

    def entry(self, entry_id: int, force: bool = False) -> Fetched:
        return self._file("entry")

    def history(self, entry_id: int, force: bool = False) -> Fetched:
        return self._file("history")

    def picks(self, entry_id: int, gw: int, final: bool = False, force: bool = False) -> Fetched:
        return self._file("picks")

    def transfers(self, entry_id: int, force: bool = False) -> Fetched:
        return self._file("transfers")

    def standings_page(self, page: int) -> Fetched:
        raise ProviderUnavailable("Rank-target standings are not part of the sample data.")

    def may_refresh(self, entry_id: int, session_id: str) -> bool:
        return False


__all__ = [
    "Gateway",
    "SampleGateway",
    "Fetched",
    "EntryNotFound",
    "ProviderUnavailable",
    "RateLimited",
    "TTL",
    "SOURCE",
    "SAMPLE_SOURCE",
]
