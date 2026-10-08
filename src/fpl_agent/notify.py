"""Alerts. Posts to NOTIFY_WEBHOOK if set (Discord/Slack-style JSON). Never raises: an alert
failing must not take the run down."""

from __future__ import annotations

import os

import httpx


def send(message: str, client: httpx.Client | None = None, webhook: str | None = None) -> bool:
    url = webhook or os.environ.get("NOTIFY_WEBHOOK")
    if not url:
        return False
    try:
        http = client or httpx.Client(timeout=10)
        resp = http.post(url, json={"content": message[:1900], "text": message[:1900]})
        return resp.status_code < 300
    except Exception:  # noqa: BLE001
        return False
