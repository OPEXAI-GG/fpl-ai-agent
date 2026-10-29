"""Alerts. Posts to NOTIFY_WEBHOOK if set (Discord/Slack-style JSON). Never raises: an alert
failing must not take the run down. Each chunk is retried a few times (network errors, 429 and
5xx, honouring a short Retry-After); `send` is True only when every chunk was accepted, so a
report that did not arrive is sent again by the next run (see runs.py)."""

from __future__ import annotations

import os
import sys
import time

import httpx

LIMIT = 1900  # Discord refuses messages over 2000 characters
ATTEMPTS = 3  # per chunk
MAX_WAIT = 10.0  # seconds; a longer Retry-After gives up and leaves it to the next run
_sleep = time.sleep  # replaced in tests


DISCORD_API = "https://discord.com/api/v10"


def interaction_url(app_id: str, token: str) -> str:
    """Follow-up address for a slash command: posts in the same conversation, no bot token."""
    return f"{DISCORD_API}/webhooks/{app_id}/{token}"


def chunks(message: str, limit: int = LIMIT) -> list[str]:
    """Split on line breaks into pieces under `limit`, so a long plan arrives whole."""
    out, cur = [], ""
    for line in message.splitlines():
        while len(line) > limit:
            out, cur = ([*out, cur] if cur else out), ""
            out.append(line[:limit])
            line = line[limit:]
        if cur and len(cur) + 1 + len(line) > limit:
            out.append(cur)
            cur = line
        else:
            cur = f"{cur}\n{line}" if cur else line
    return [*out, cur] if cur else out


def _post(http: httpx.Client, url: str, part: str) -> httpx.Response:
    """One chunk, retried on network errors, 429 and 5xx with a capped backoff."""
    for attempt in range(ATTEMPTS):
        last = attempt == ATTEMPTS - 1
        try:
            resp = http.post(url, json={"content": part, "text": part})
        except httpx.TransportError:
            if last:
                raise
            _sleep(2**attempt)
            continue
        if resp.status_code != 429 and resp.status_code < 500 or last:
            return resp
        try:
            wait = float(resp.headers.get("Retry-After", 2**attempt))
        except ValueError:
            wait = 2.0**attempt
        if wait > MAX_WAIT:
            return resp
        _sleep(wait)
    return resp


def send(message: str, client: httpx.Client | None = None, webhook: str | None = None) -> bool:
    url = webhook or os.environ.get("NOTIFY_WEBHOOK")
    if not url:
        print("notify: no webhook set, nothing sent", file=sys.stderr)
        return False
    try:
        http = client or httpx.Client(timeout=10)
        for part in chunks(message):
            resp = _post(http, url, part)
            if resp.status_code >= 300:
                break
    except Exception as exc:  # noqa: BLE001
        print(f"notify: not sent ({type(exc).__name__})", file=sys.stderr)
        return False
    ok = resp.status_code < 300
    detail = (
        "sent" if ok else f"refused, HTTP {resp.status_code}: {' '.join(resp.text.split())[:120]}"
    )
    print(f"notify: {detail}", file=sys.stderr)  # never the webhook address itself
    return ok
