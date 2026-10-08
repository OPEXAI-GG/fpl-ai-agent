"""A manager's team as FPL's public endpoints describe it: the squad, selling prices, bank, free
transfers and which chips are left. Nothing here needs a login.

* squad: the picks of the last deadline (what the manager actually had);
* purchase price: the latest transfer-in of that player, or the opening price for the original
  squad (`now_cost - cost_change_start`); selling price = engine.selling_price (half the profit);
* free transfers: replayed from the transfer history (1 banked per gameweek, max 5, a Wildcard or
  Free Hit week uses none);
* chips: from the history, each chip type allowed once per half-season window.

The state is *before* any transfer made for the upcoming gameweek; transfers already made for it
are reported (`already_transferred`) so a human edit is not overwritten blindly.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from fpl_agent.data.players import POSITIONS
from fpl_agent.rules import Rules, selling_price
from fpl_agent.solver.planner import SquadPlayer


@dataclass
class SquadState:
    next_gw: int
    squad: list[SquadPlayer]
    bank: int
    free_transfers: int
    chips_used: list[tuple[str, int]] = field(default_factory=list)
    value: int = 0
    already_transferred: int = 0
    picks: list[dict[str, Any]] = field(default_factory=list)

    def chips_available(self, rules: Rules) -> dict[str, tuple[int, int]]:
        """Chip -> (first gw, last gw) of the window it may still be played in."""
        out = {}
        for chip, windows in rules["chips"].items():
            for lo, hi in windows:
                if lo <= self.next_gw <= hi:
                    used = any(c == chip and lo <= ev <= hi for c, ev in self.chips_used)
                    if not used:
                        out[chip] = (max(lo, self.next_gw), hi)
        return out


def free_transfers_at(history: dict[str, Any], next_gw: int, max_banked: int = 5) -> int:
    """Free transfers available at the start of `next_gw`, replayed from the history."""
    chips = {c["event"]: c["name"] for c in history.get("chips", [])}
    made = {r["event"]: r["event_transfers"] for r in history.get("current", [])}
    ft = 1
    for gw in range(2, next_gw):
        used = 0 if chips.get(gw) in ("wildcard", "freehit") else min(made.get(gw, 0), ft)
        ft = min(max_banked, ft - used + 1)
    return ft


def parse_squad(
    picks: dict[str, Any],
    transfers: list[dict[str, Any]],
    history: dict[str, Any],
    bootstrap: dict[str, Any],
    next_gw: int,
    rules: Rules,
) -> SquadState:
    els = {e["id"]: e for e in bootstrap["elements"]}
    last_gw = next_gw - 1
    bought: dict[int, int] = {}
    already = 0
    for t in sorted(transfers, key=lambda t: t["time"]):
        if t["event"] >= next_gw:
            already += 1
            continue
        bought[t["element_in"]] = t["element_in_cost"]
    squad = []
    for p in picks["picks"]:
        e = els[p["element"]]
        buy = bought.get(e["id"], e["now_cost"] - e.get("cost_change_start", 0))
        squad.append(
            SquadPlayer(
                e["id"],
                POSITIONS[e["element_type"]],
                e["team"],
                buy,
                selling_price(buy, e["now_cost"], rules),
            )  # fmt: skip
        )
    hist = picks.get("entry_history", {})
    chips = [(c["name"], c["event"]) for c in history.get("chips", []) if c["event"] <= last_gw]
    return SquadState(
        next_gw=next_gw, squad=squad, bank=int(hist.get("bank", 0)),
        free_transfers=free_transfers_at(history, next_gw, rules["transfers"]["max_banked"]),
        chips_used=chips, value=int(hist.get("value", 0)), already_transferred=already,
        picks=picks["picks"],
    )  # fmt: skip
