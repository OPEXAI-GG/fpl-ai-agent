"""Alerts. Posts to NOTIFY_WEBHOOK if set (Discord/Slack-style JSON). Never raises: an alert
failing must not take the run down."""

from __future__ import annotations

import os
import sys

import httpx


def send(message: str, client: httpx.Client | None = None, webhook: str | None = None) -> bool:
    url = webhook or os.environ.get("NOTIFY_WEBHOOK")
    if not url:
        print("notify: no webhook set, nothing sent", file=sys.stderr)
        return False
    try:
        http = client or httpx.Client(timeout=10)
        resp = http.post(url, json={"content": message[:1900], "text": message[:1900]})
    except Exception as exc:  # noqa: BLE001
        print(f"notify: not sent ({type(exc).__name__})", file=sys.stderr)
        return False
    ok = resp.status_code < 300
    detail = (
        "sent" if ok else f"refused, HTTP {resp.status_code}: {' '.join(resp.text.split())[:120]}"
    )
    print(f"notify: {detail}", file=sys.stderr)  # never the webhook address itself
    return ok
