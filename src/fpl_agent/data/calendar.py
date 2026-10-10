"""Deadline calendar derived from bootstrap-static events."""

from __future__ import annotations

from datetime import UTC, datetime

from fpl_agent.data.schemas import Bootstrap, Event


def next_event(bs: Bootstrap) -> Event | None:
    for ev in bs.events:
        if ev.is_next:
            return ev
    return None


def hours_to_deadline(ev: Event, now: datetime | None = None) -> float:
    now = now or datetime.now(UTC)
    return (ev.deadline_time - now).total_seconds() / 3600


def in_action_window(
    ev: Event, now: datetime | None = None, lo_hours: float = 0.75, hi_hours: float = 26
) -> bool:
    """True when the deadline is between lo_hours and hi_hours away. The scheduled runs every
    two hours re-plan in that window, so the last one lands 45 minutes to under 3 hours before
    the deadline, after the pre-match press conferences and with the latest team news."""
    return lo_hours <= hours_to_deadline(ev, now) <= hi_hours


def upcoming_deadlines(bs: Bootstrap, now: datetime | None = None) -> list[tuple[int, datetime]]:
    now = now or datetime.now(UTC)
    return [(e.id, e.deadline_time) for e in bs.events if e.deadline_time > now]
