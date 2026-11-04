"""Scout: reads FPL's injury/news text and returns a probability that each player plays.

It is a bounded helper, not a decision maker:
* one Claude API call per run, at most `limit` players, a small output cap;
* the answer is validated; anything malformed is discarded;
* it can only move a player's availability within +-`max_shift` of FPL's own flag, and never
  resurrects a player FPL lists as left/unavailable;
* with no `ANTHROPIC_API_KEY`, or on any failure, the pool is returned unchanged.
The adjusted `chance` feeds the minutes model's availability overlay, nothing else.
"""

from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass

import httpx
import pandas as pd

log = logging.getLogger(__name__)
URL = "https://api.anthropic.com/v1/messages"
DEFAULT_MODEL = "claude-sonnet-5-5"

SYSTEM = (
    "You assess Premier League injury and team news for Fantasy Premier League. For each player "
    "you are given FPL's status flag, its chance-of-playing number and its news text. Return ONLY "
    'a JSON list: [{"id": <int>, "p_play": <0..1 probability he plays real minutes next '
    'gameweek>, "reason": "<8 words>"}]. Be conservative: use only the text given, never invent '
    "facts. If the text gives no information beyond the flag, repeat the flag's implied value."
)


@dataclass(frozen=True)
class Adjustment:
    player_id: int
    p_play: float
    reason: str


class Scout:
    def __init__(
        self,
        key: str | None = None,
        client: httpx.Client | None = None,
        model: str | None = None,
        limit: int = 40,
        max_shift: float = 0.35,
    ) -> None:
        self.key = key or os.environ.get("ANTHROPIC_API_KEY") or None
        self.model = model or os.environ.get("SCOUT_MODEL", DEFAULT_MODEL)
        self.limit, self.max_shift = limit, max_shift
        self._http = client or httpx.Client(timeout=60)

    @property
    def enabled(self) -> bool:
        return bool(self.key)

    def select(self, pool: pd.DataFrame) -> pd.DataFrame:
        """Players whose news could change a decision: flagged, or carrying news text."""
        news = pool["news"].fillna("").astype(str).str.strip() != ""
        flagged = (pool["status"] != "a") | (pool["chance"].fillna(100) < 100)
        sel = pool[news & flagged]
        return sel.sort_values("price", ascending=False).head(self.limit)

    def ask(self, items: pd.DataFrame) -> list[Adjustment]:
        if items.empty or not self.enabled:
            return []
        lines = [
            f'{{"id": {int(r.player_id)}, "status": "{r.status}", "chance": {r.chance}, '
            f'"news": {json.dumps(str(r.news))}}}'
            for r in items.itertuples()
        ]
        body = {"model": self.model, "max_tokens": 1500, "system": SYSTEM,
                "messages": [{"role": "user", "content": "\n".join(lines)}]}  # fmt: skip
        try:
            r = self._http.post(URL, json=body, headers={
                "x-api-key": self.key, "anthropic-version": "2023-06-01"})  # fmt: skip
            r.raise_for_status()
            text = "".join(b.get("text", "") for b in r.json().get("content", []))
        except (httpx.HTTPError, ValueError) as exc:
            log.warning("scout call failed: %s", type(exc).__name__)
            return []
        return parse(text, set(items["player_id"].astype(int)))

    def apply(self, pool: pd.DataFrame, adjustments: list[Adjustment]) -> pd.DataFrame:
        """Copy of `pool` with `chance` moved (bounded) toward the scout's view."""
        out = pool.copy()
        by_id = {a.player_id: a for a in adjustments}
        for idx, row in out.iterrows():
            a = by_id.get(int(row["player_id"]))
            if a is None or row["status"] in ("n", "u"):
                continue
            base = 1.0 if pd.isna(row["chance"]) else float(row["chance"]) / 100.0
            p = min(max(a.p_play, base - self.max_shift), base + self.max_shift)
            out.at[idx, "chance"] = round(100 * min(max(p, 0.0), 1.0), 1)
            if out.at[idx, "status"] == "a" and p < 1.0:
                out.at[idx, "status"] = "d"
        return out

    def run(self, pool: pd.DataFrame) -> tuple[pd.DataFrame, list[Adjustment]]:
        if not self.enabled:
            return pool, []
        adj = self.ask(self.select(pool))
        return (self.apply(pool, adj) if adj else pool), adj


def parse(text: str, allowed: set[int]) -> list[Adjustment]:
    """Strictly validate the model's JSON; unknown ids and out-of-range values are dropped."""
    m = re.search(r"\[.*\]", text, re.S)
    if not m:
        return []
    try:
        raw = json.loads(m.group(0))
    except ValueError:
        return []
    out = []
    for item in raw if isinstance(raw, list) else []:
        try:
            pid, p = int(item["id"]), float(item["p_play"])
        except (KeyError, TypeError, ValueError):
            continue
        if pid in allowed and 0.0 <= p <= 1.0:
            out.append(Adjustment(pid, p, str(item.get("reason", ""))[:80]))
    return out
