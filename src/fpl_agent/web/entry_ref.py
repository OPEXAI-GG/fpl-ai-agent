"""Visitor input -> an FPL entry ID.

Accepts a bare number or a link to a team on the official FPL site (with or without scheme,
`www.` or a trailing path such as `/event/10` or `/history`). The link is only parsed, never
fetched: every request to FPL goes through `provider`, which builds its own URLs.

An entry ID gives access to public information only. It does not prove who owns the team.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import urlsplit

MAX_ENTRY = 99_999_999  # FPL ids are well below this; anything longer is not an entry
FPL_HOSTS = {"fantasy.premierleague.com", "www.fantasy.premierleague.com"}
_PATH = re.compile(r"^/(?:entry|team)/(\d{1,9})(?:/(?:event/\d{1,2}|history|transfers)?/?)?$")


@dataclass(frozen=True)
class EntryRef:
    entry_id: int
    source: str  # "id" | "url"


class InvalidEntry(ValueError):
    """The input is not an entry ID or a supported FPL team link (message is user-facing)."""


def parse_entry(text: str | int | None) -> EntryRef:
    if isinstance(text, int) and not isinstance(text, bool):
        return EntryRef(_check(text), "id")
    s = (text or "").strip() if isinstance(text, str) else ""
    if not s:
        raise InvalidEntry("Enter your FPL team ID or paste the link to your team.")
    if len(s) > 200:
        raise InvalidEntry("That input is too long to be an FPL team ID or link.")
    if s.isdigit():
        return EntryRef(_check(int(s)), "id")
    if "." not in s and "/" not in s:
        raise InvalidEntry(
            "That doesn't look like an FPL team ID or link. The ID is the number in your "
            "team's Points page link."
        )
    url = s if "://" in s else "https://" + s
    try:
        parts = urlsplit(url)
    except ValueError as exc:
        raise InvalidEntry("That doesn't look like an FPL team ID or link.") from exc
    host = (parts.hostname or "").lower()
    if parts.scheme not in ("http", "https") or host not in FPL_HOSTS:
        raise InvalidEntry(
            "Only links to fantasy.premierleague.com are supported. You can also type the "
            "number from your team's points page."
        )
    if parts.username or parts.password or parts.port not in (None, 80, 443):
        raise InvalidEntry("That link contains extra login or port details; paste the plain link.")
    m = _PATH.match(parts.path or "/")
    if not m:
        raise InvalidEntry(
            "That FPL link doesn't include a team ID. Open your team's Points page and copy "
            "its link, which contains /entry/<number>/."
        )
    return EntryRef(_check(int(m.group(1))), "url")


def _check(n: int) -> int:
    if not 1 <= n <= MAX_ENTRY:
        raise InvalidEntry("FPL team IDs are positive whole numbers of up to eight digits.")
    return n


__all__ = ["EntryRef", "InvalidEntry", "parse_entry", "MAX_ENTRY"]
