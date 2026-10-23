"""Rank-target mode: how far you are behind a target overall rank, and how much risk that buys.

The squad keeps following what the top managers own (the core that scores the points; see
`decide.rank_risk`). The catch-up risk goes where it is cheap and bounded: the armband. While
you are behind the target, the captain is chosen among the players within `captain_slack`
expected points of the best one, preferring the one the field has not captained (a differential
armband gains on everyone when he hauls). The slack grows with the points needed per gameweek
and is capped, so the expected cost stays below a point a week.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

OVERALL_LEAGUE = 314
PAGE_SIZE = 50
TARGET_RANK = 10_000  # AGENT_RANK_TARGET overrides
LAST_GW = 38
SLACK_PER_POINT = 0.15  # captain slack per point a week you must gain on the target
MAX_SLACK = 1.0
CHASE_RISK = -0.15  # negative: the armband rewards differing from the field's captaincy


@dataclass(frozen=True)
class Chase:
    target: int
    rank: int | None
    points: int
    target_points: int
    weeks_left: int

    @property
    def gap(self) -> int:
        return self.target_points - self.points

    @property
    def per_week(self) -> float:
        return self.gap / max(self.weeks_left, 1)

    @property
    def behind(self) -> bool:
        return self.gap > 0

    @property
    def captain_slack(self) -> float:
        return min(MAX_SLACK, SLACK_PER_POINT * self.per_week) if self.behind else 0.0

    def note(self) -> str:
        where = f"overall rank {self.rank:,}, " if self.rank else ""
        if not self.behind:
            return (f"rank target top {self.target:,}: {where}{-self.gap} points clear; "
                    "playing it safe")  # fmt: skip
        return (f"rank target top {self.target:,}: {where}{self.gap} points behind "
                f"({self.per_week:.1f} a week over {self.weeks_left} gameweeks); the captain "
                f"is the least-captained option within {self.captain_slack:.1f} expected "
                "points of the best")  # fmt: skip


def target_rank() -> int:
    raw = os.environ.get("AGENT_RANK_TARGET", "").strip().replace(",", "").replace("_", "")
    return int(raw) if raw.isdigit() and int(raw) > 0 else TARGET_RANK


def target_points(fpl, target: int) -> int | None:
    """Total points of the manager at the target overall rank (from the standings page)."""
    page = (target - 1) // PAGE_SIZE + 1
    rows = fpl.league_standings_raw(OVERALL_LEAGUE, page).get("standings", {}).get("results", [])
    if not rows:
        return None
    at = [r for r in rows if r.get("rank", 0) <= target] or rows[:1]
    return int(at[-1]["total"])


def chase(fpl, history: dict, next_gw: int, target: int | None = None) -> Chase | None:
    """Where you stand against the target, or None when it cannot be read."""
    played = [r for r in history.get("current", []) if r.get("total_points") is not None]
    if not played:
        return None
    target = target or target_rank()
    pts = target_points(fpl, target)
    if pts is None:
        return None
    last = played[-1]
    return Chase(target=target, rank=last.get("overall_rank"), points=int(last["total_points"]),
                 target_points=pts, weeks_left=LAST_GW - next_gw + 1)  # fmt: skip
