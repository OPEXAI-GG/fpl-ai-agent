"""The team as FPL's public endpoints describe it, with what each part means.

The public picks are the squad, lineup and bank *at the last deadline*. Transfers confirmed
since then appear in the public transfer list, so the current squad is the last deadline's with
those applied; its bank and selling prices follow from FPL's pricing rule. The free-transfer
count is not published at all: it is reconstructed from the transfer history and stays an
estimate. Nothing here needs a login.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from fpl_agent.data.entry import parse_squad
from fpl_agent.rules import Rules, selling_price


def _ts(s: str | None) -> datetime | None:
    if not s:
        return None
    return datetime.fromisoformat(s.replace("Z", "+00:00")).astimezone(UTC)


@dataclass
class PublicView:
    entry: int
    gameweek: int  # the upcoming gameweek
    fetched_at: datetime
    deadline_squad: list[int]  # the picks of the last deadline (FPL ids)
    deadline_lineup: tuple[list[int], list[int], int | None, int | None] | None
    squad: list[int]  # the last deadline's squad with this week's public transfers applied
    buy: dict[int, int]  # purchase price per current player (tenths)
    sell: dict[int, int]  # estimated selling price per current player (tenths)
    bank: int  # the last deadline's bank, adjusted for this week's public transfers
    ft_estimate: int  # reconstructed free transfers at the start of the week (not published)
    transfers: list[dict[str, Any]] = field(default_factory=list)  # this week's, oldest first
    chips_used: list[tuple[str, int]] = field(default_factory=list)
    sold_for: dict[int, int] = field(
        default_factory=dict
    )  # estimated sale price of this week's outs
    deadline_time: datetime | None = None  # of the last deadline
    value: int = 0

    @property
    def as_of(self) -> datetime | None:
        """When the public squad last changed: its newest transfer, else the last deadline."""
        times = [_ts(t.get("time")) for t in self.transfers]
        times = [t for t in times if t]
        return max(times) if times else self.deadline_time

    def transfers_after(self, t: datetime) -> list[dict[str, Any]]:
        return [x for x in self.transfers if (_ts(x.get("time")) or t) > t]


def public_view(
    entry: int,
    gameweek: int,
    picks: dict,
    transfers: list[dict],
    history: dict,
    bootstrap: dict,
    rules: Rules,
    fetched_at: datetime,
) -> PublicView:
    """Build the public view from the raw responses the weekly run already reads."""
    state = parse_squad(picks, transfers, history, bootstrap, gameweek, rules)
    els = {e["id"]: e for e in bootstrap["elements"]}
    buy = {p.player_id: p.buy_price for p in state.squad}
    sell = {p.player_id: p.sell_price for p in state.squad}
    squad = [p.player_id for p in state.squad]
    week = sorted(
        (t for t in transfers if t.get("event", 0) >= gameweek),
        key=lambda t: (t.get("time") or "", t.get("element_in", 0)),
    )
    bank = state.bank
    sold_for: dict[int, int] = {}
    for t in week:  # apply each confirmed transfer in order
        out, inn = t["element_out"], t["element_in"]
        if out in squad:
            squad.remove(out)
            sold_for[out] = sell.pop(out, 0)
            bank += sold_for[out]
            buy.pop(out, None)
        squad.append(inn)
        buy[inn] = int(t["element_in_cost"])
        sell[inn] = selling_price(buy[inn], els[inn]["now_cost"], rules) if inn in els else buy[inn]
        bank -= buy[inn]
    lineup = None
    pk = picks.get("picks") or []
    if len(pk) == 15:
        order = sorted(pk, key=lambda p: p.get("position", 0))
        xi = sorted(p["element"] for p in order[:11])
        bench = [p["element"] for p in order[11:]]
        cap = next((p["element"] for p in pk if p.get("is_captain")), None)
        vice = next((p["element"] for p in pk if p.get("is_vice_captain")), None)
        lineup = (xi, bench, cap, vice)
    last = next((e for e in bootstrap.get("events", []) if e.get("id") == gameweek - 1), None)
    return PublicView(
        entry=entry,
        gameweek=gameweek,
        fetched_at=fetched_at,
        deadline_squad=sorted(p["element"] for p in pk),
        deadline_lineup=lineup,
        squad=sorted(squad),
        buy=buy,
        sell=sell,
        bank=bank,
        ft_estimate=state.free_transfers,
        transfers=week,
        chips_used=state.chips_used,
        sold_for=sold_for,
        deadline_time=_ts(last.get("deadline_time")) if last else None,
        value=state.value,
    )


__all__ = ["PublicView", "public_view"]
