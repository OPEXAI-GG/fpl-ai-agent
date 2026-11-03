"""X (formerly Twitter) API v2: recent posts of explicitly configured accounts.

Access: app-only bearer token in X_BEARER_TOKEN (developer.x.com). Endpoints used:

* `GET /2/users/by/username/{username}` once per account (the numeric id is cached in the cursor)
* `GET /2/users/{id}/tweets` with `since_id` (incremental), `pagination_token` (bounded by
  `max_pages`), `max_results` (5..100), replies excluded.

Resuming: when a poll stops at `max_pages` with a continuation token left, the cursor keeps
that window as `pending` (its since_id floor, the newest id seen, the token) and `since_id`
does not move. The next poll finishes the pending window first; only when it is complete does
`since_id` advance to its newest id, so an older post inside a capped window is never skipped.
A failed request saves nothing, so the same window is retried.

Edits: X gives an edited post a new id and lists the chain in `edit_history_tweet_ids` (newer
specs call it `edit_history_post_ids`); the first id of the chain is the stable provider id, so an
edit is stored as a new revision of the same item. Reposts and quotes carry the original's id in
`repost_of`, so a copy never counts as independent confirmation.

Without a token the source raises `MissingCredentials` and is skipped. Live access was not
verified when this was written (no token was available); the contract is covered by tests
against recorded-shape responses.
"""

from __future__ import annotations

import json
import os
from datetime import datetime

import httpx

from fpl_agent.errors import SourceFormatError
from fpl_agent.news import Item
from fpl_agent.news.fetch import MissingCredentials, get
from fpl_agent.news.registry import SourceConfig
from fpl_agent.pit import utc

API = "https://api.x.com/2"
FIELDS = "created_at,author_id,edit_history_tweet_ids,referenced_tweets,note_tweet,lang"


def _json(body: bytes, what: str) -> dict:
    try:
        data = json.loads(body)
    except ValueError as exc:
        raise SourceFormatError(f"x {what}: response is not JSON") from exc
    if not isinstance(data, dict):
        raise SourceFormatError(f"x {what}: unexpected response shape")
    return data


def post_item(p: dict, username: str) -> Item:
    if not isinstance(p, dict) or "id" not in p or "text" not in p:
        raise SourceFormatError("x post without id/text")
    chain = p.get("edit_history_tweet_ids") or p.get("edit_history_post_ids") or [p["id"]]
    original = str(chain[0])
    text = (p.get("note_tweet") or {}).get("text") or p["text"]
    ref = next((r for r in p.get("referenced_tweets") or []
                if r.get("type") in ("retweeted", "quoted")), None)  # fmt: skip
    return Item(
        "x", original, title=text[:140], content=text[:4000],
        canonical_url=f"https://x.com/{username}/status/{p['id']}", author=f"@{username}",
        published_at=utc(p.get("created_at")), repost_of=str(ref["id"]) if ref else None,
        raw={"id": p["id"], "edit_chain": [str(c) for c in chain], "lang": p.get("lang")},
    )  # fmt: skip


def fetch(
    http: httpx.Client, src: SourceConfig, state: dict, now: datetime, token: str | None = None
) -> tuple[list[Item], dict, int]:
    """(items, new cursor state, requests made) across the source's accounts."""
    token = token or os.environ.get("X_BEARER_TOKEN") or None
    if not token:
        raise MissingCredentials("X_BEARER_TOKEN is not set")
    if not src.accounts:
        return [], state, 0
    headers = {"Authorization": f"Bearer {token}"}
    cursor = json.loads(state.get("cursor") or "{}")
    items: list[Item] = []
    requests = 0
    for username in src.accounts:
        acc = cursor.get(username, {})
        if not acc.get("id"):
            r = get(http, f"{API}/users/by/username/{username}", now, headers=headers,
                    timeout=src.timeout_seconds, max_bytes=src.max_bytes)  # fmt: skip
            requests += 1
            data = _json(r.body, f"user {username}").get("data") or {}
            if not data.get("id"):
                raise SourceFormatError(f"x user {username}: no id in response")
            acc["id"] = str(data["id"])
        params: dict = {"max_results": max(5, min(src.max_items, 100)), "tweet.fields": FIELDS,
                        "exclude": "replies"}  # fmt: skip
        pending = acc.get("pending")
        # a window capped by max_pages last time is finished first: same since_id floor, the
        # stored continuation token; the high-water mark moves only once it is complete
        floor = pending["floor"] if pending else acc.get("since_id")
        newest = pending["newest"] if pending else None
        if floor:
            params["since_id"] = floor
        if pending:
            params["pagination_token"] = pending["next_token"]
        nxt = None
        for _ in range(src.max_pages):
            r = get(http, f"{API}/users/{acc['id']}/tweets", now, params=params, headers=headers,
                    timeout=src.timeout_seconds, max_bytes=src.max_bytes)  # fmt: skip
            requests += 1
            page = _json(r.body, f"timeline {username}")
            posts = page.get("data") or []
            if not isinstance(posts, list):
                raise SourceFormatError(f"x timeline {username}: data is not a list")
            items += [post_item(p, username) for p in posts]
            meta = page.get("meta") or {}
            ids = [int(p["id"]) for p in posts if str(p.get("id", "")).isdigit()]
            if meta.get("newest_id"):
                ids.append(int(meta["newest_id"]))
            if ids and (newest is None or max(ids) > int(newest)):
                newest = str(max(ids))
            nxt = meta.get("next_token")
            if not nxt:
                break
            params = {**params, "pagination_token": nxt}
        acc.pop("pending", None)
        if nxt:  # capped: keep the window open, the mark stays where it was
            acc["pending"] = {"floor": floor, "newest": newest, "next_token": nxt}
            acc["since_id"] = floor
        else:
            acc["since_id"] = newest or floor
        if acc.get("since_id") is None:
            acc.pop("since_id", None)
        cursor[username] = acc
    return items, {**state, "cursor": json.dumps(cursor)}, requests
