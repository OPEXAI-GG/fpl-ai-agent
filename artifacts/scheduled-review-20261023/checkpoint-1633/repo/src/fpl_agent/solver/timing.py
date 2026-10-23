"""When to make the week's transfers: now, or after the press conferences.

A transfer made a day early can buy a player who is ruled out on Friday. So the scheduled runs plan
from about 26 hours before the deadline but hold the moves until the last few hours, unless waiting
costs money: FPL changes prices once a night, and a player in the plan who is about to rise (bought
in) or fall (sold) is worth moving before that update.

Price changes follow net transfers relative to how many managers own the player. FPL does not
publish the thresholds; the share below is a rough rule of thumb, to be tuned once daily price
snapshots are logged.
"""

from __future__ import annotations

from datetime import UTC, datetime, time, timedelta

LAST_CALL = 5.0  # hours before the deadline from which moves are always made
PRICE_UPDATE = time(1, 30, tzinfo=UTC)  # FPL's nightly price update, roughly
CHANGE_SHARE = 0.05  # net transfers this gameweek, as a share of owners, for one price change
MIN_NET = 20_000  # never call a change on fewer net transfers than this


def price_pressure(raw: dict) -> dict[int, int]:
    """FPL id -> +1 likely to rise at the next update, -1 likely to fall, 0 neither.

    Progress is net transfers this gameweek over CHANGE_SHARE of owners, less the changes already
    made this gameweek in that direction (each one resets the count)."""
    total = raw.get("total_players") or 0
    out: dict[int, int] = {}
    for e in raw.get("elements", []):
        owners = float(e.get("selected_by_percent") or 0) / 100 * total
        net = (e.get("transfers_in_event") or 0) - (e.get("transfers_out_event") or 0)
        if not owners or abs(net) < MIN_NET:
            continue
        sign = 1 if net > 0 else -1
        done = max(0, sign * (e.get("cost_change_event") or 0))  # tenths already moved this way
        if abs(net) / (CHANGE_SHARE * owners) - done >= 1:
            out[int(e["id"])] = sign
    return out


def update_before(deadline: datetime, now: datetime) -> bool:
    """True when a nightly price update falls between now and the deadline."""
    day = datetime.combine(now.date(), PRICE_UPDATE)
    nxt = day if day > now else day + timedelta(days=1)
    return nxt < deadline


def hold_moves(transfers_in: list[int], transfers_out: list[int], pressure: dict[int, int],
               hours: float, price_update: bool, name=str) -> tuple[bool, str]:  # fmt: skip
    """(hold, why). Holds while the deadline is more than LAST_CALL hours away and no player in
    the plan is about to change price the wrong way before it."""
    if not transfers_in:
        return False, ""
    if hours <= LAST_CALL:
        return False, ""
    risers = [i for i in transfers_in if pressure.get(i) == 1]
    fallers = [o for o in transfers_out if pressure.get(o) == -1]
    if price_update and (risers or fallers):
        why = [f"{name(i)} likely to rise" for i in risers]
        why += [f"{name(o)} likely to fall" for o in fallers]
        return False, f"moves made now, before tonight's price update: {', '.join(why)}"
    return True, (
        f"moves held for team news: the deadline is {hours:.0f}h away, they are re-planned and "
        f"made by the run {LAST_CALL:.0f}h or less before it"
    )
