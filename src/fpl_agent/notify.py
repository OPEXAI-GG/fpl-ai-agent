"""Alerts. Posts to NOTIFY_WEBHOOK if set (Discord/Slack-style JSON). Never raises: an alert
failing must not take the run down."""

from __future__ import annotations

import os
import sys

import httpx

LIMIT = 1900  # Discord refuses messages over 2000 characters


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


def send(message: str, client: httpx.Client | None = None, webhook: str | None = None) -> bool:
    url = webhook or os.environ.get("NOTIFY_WEBHOOK")
    if not url:
        print("notify: no webhook set, nothing sent", file=sys.stderr)
        return False
    try:
        http = client or httpx.Client(timeout=10)
        for part in chunks(message):
            resp = http.post(url, json={"content": part, "text": part})
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
