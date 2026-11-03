"""Bounded HTTP for news adapters: a size cap, a timeout, conditional requests and typed
rate-limit and availability errors. Nothing here retries in a loop: a source that is rate
limited waits until the time the server gave (stored in `source_state.next_allowed_at`)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import httpx

from fpl_agent.errors import SourceFormatError, SourceUnavailable
from fpl_agent.pit import utc

USER_AGENT = "fpl-agent/0.1 (+https://github.com/OPEXAI-GG/fpl-ai-agent)"


class RateLimited(SourceUnavailable):
    """The source asked us to slow down; `retry_at` (naive UTC) says until when."""

    def __init__(self, message: str, retry_at: datetime) -> None:
        super().__init__(message)
        self.retry_at = retry_at


class MissingCredentials(SourceUnavailable):
    """An optional source needs a credential that is not configured: skipped, not failed."""


@dataclass
class Response:
    status: int
    body: bytes
    headers: dict[str, str]

    @property
    def not_modified(self) -> bool:
        return self.status == 304


def retry_at(resp: httpx.Response, now: datetime) -> datetime:
    """When the server says we may ask again: Retry-After (seconds or a date) or X's
    x-rate-limit-reset (epoch seconds); 15 minutes when it gives neither."""
    ra = resp.headers.get("retry-after")
    if ra:
        if ra.strip().isdigit():
            return now + timedelta(seconds=int(ra))
        try:
            from email.utils import parsedate_to_datetime

            return utc(parsedate_to_datetime(ra)) or now + timedelta(minutes=15)
        except (TypeError, ValueError):
            pass
    reset = resp.headers.get("x-rate-limit-reset")
    if reset and reset.strip().isdigit():
        return datetime.fromtimestamp(int(reset), UTC).replace(tzinfo=None)
    return now + timedelta(minutes=15)


def get(
    http: httpx.Client,
    url: str,
    now: datetime,
    params: dict | None = None,
    headers: dict | None = None,
    timeout: float = 20.0,
    max_bytes: int = 2_000_000,
) -> Response:
    h = {"User-Agent": USER_AGENT, **(headers or {})}
    try:
        with http.stream("GET", url, params=params, headers=h, timeout=timeout) as resp:
            if resp.status_code == 429:
                raise RateLimited(f"GET {url}: rate limited (429)", retry_at(resp, now))
            if resp.status_code == 304:
                return Response(304, b"", dict(resp.headers))
            if resp.status_code in (401, 403):
                raise SourceUnavailable(f"GET {url}: not authorised ({resp.status_code})")
            if resp.status_code >= 500 or resp.status_code == 404:
                raise SourceUnavailable(f"GET {url}: HTTP {resp.status_code}")
            if resp.status_code >= 400:
                raise SourceFormatError(f"GET {url}: HTTP {resp.status_code}")
            chunks, size = [], 0
            for chunk in resp.iter_bytes():
                size += len(chunk)
                if size > max_bytes:
                    raise SourceFormatError(f"GET {url}: response larger than {max_bytes} bytes")
                chunks.append(chunk)
            return Response(resp.status_code, b"".join(chunks), dict(resp.headers))
    except httpx.HTTPError as exc:
        raise SourceUnavailable(f"GET {url}: {type(exc).__name__}") from exc
