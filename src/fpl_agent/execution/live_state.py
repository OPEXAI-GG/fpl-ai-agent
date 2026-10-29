"""Your FPL team as FPL itself says it is right now (live mode only).

The public pages only show the squad as it was at the last deadline. Anything done since, by
you in the app or by an earlier run, is invisible there, and a plan built on the old squad
asks FPL to sell players you no longer own. So in live mode the run signs in first, reads the
current team, and plans from that. If the team differs from the last deadline's, transfers
were already made for this gameweek: no further ones are sent and no chip is planned, only the
lineup and captain are set. The exception is an active Wildcard: until the deadline every change
is free, so each run re-plans the whole squad from the latest data and news.

Whatever chip is already active is carried into the plan (`active_chip`) so a re-plan keeps it
and never plays a second one. An active Free Hit's squad is the bot's temporary one: it is not
re-planned or counted as your own changes, and FPL restores the last deadline's squad after it.
"""

from __future__ import annotations

import httpx

from fpl_agent.data.entry import SquadState
from fpl_agent.data.players import POSITIONS
from fpl_agent.execution.auth import AuthError, TokenExchange, Tokens
from fpl_agent.execution.fpl_writer import FPLWriter
from fpl_agent.rules import Rules, selling_price
from fpl_agent.solver.planner import SquadPlayer


class Signed:
    """An already completed sign-in, in the shape `execute` expects (one sign-in per run: the
    login token is single-use)."""

    def __init__(self, tokens: Tokens) -> None:
        self._t = tokens

    def exchange(self) -> Tokens:
        return self._t


def overlay(state: SquadState, my: dict, raw: dict, rules: Rules) -> tuple[SquadState, bool, str]:
    """`state` with the current team from FPL's my-team reply. Returns (state, changed, note)."""
    els = {e["id"]: e for e in raw["elements"]}
    picks = my.get("picks") or []
    if len(picks) != 15 or any(p.get("element") not in els for p in picks):
        return state, False, "live team unreadable: planned from the last deadline's squad"
    squad = []
    for p in picks:
        e = els[p["element"]]
        buy = int(p.get("purchase_price") or e["now_cost"])
        sell = int(p.get("selling_price") or selling_price(buy, e["now_cost"], rules))
        squad.append(SquadPlayer(e["id"], POSITIONS[e["element_type"]], e["team"], buy, sell))
    t = my.get("transfers") or {}
    bank = int(t["bank"]) if t.get("bank") is not None else state.bank
    value = int(t["value"]) if t.get("value") is not None else state.value
    limit = t.get("limit")
    ft = limit if isinstance(limit, int) and not t.get("made") and 0 <= limit <= 5 else None
    chips = list(state.chips_used)
    for c in my.get("chips") or []:
        if c.get("status_for_entry") in ("played", "active") and c.get("name"):
            played = c.get("played_by_entry") or [state.next_gw]
            chips += [(c["name"], ev) for ev in played if (c["name"], ev) not in chips]
    active = next((
        c["name"] for c in my.get("chips") or []
        if c.get("name") and (
            c.get("status_for_entry") == "active"
            or (c.get("status_for_entry") == "played"
                and state.next_gw in (c.get("played_by_entry") or []))
        )
    ), None)  # fmt: skip
    wc = active == "wildcard"
    before = {p.player_id for p in state.squad}
    moved = sum(p.player_id not in before for p in squad)
    out = SquadState(
        next_gw=state.next_gw, squad=squad, bank=bank,
        free_transfers=state.free_transfers if ft is None else ft, chips_used=chips,
        value=value, already_transferred=0 if wc else max(state.already_transferred, moved),
        wildcard_active=wc, base_squad=list(state.squad), picks=picks, active_chip=active,
    )  # fmt: skip
    note = (
        f"your Free Hit is active (team differs from the last deadline's by {moved} player(s)): "
        "the Free Hit squad is kept, only the lineup and captain are set"
        if active == "freehit"
        else f"your Wildcard is active (team differs from the last deadline's by {moved} "
        "player(s)): re-planning the whole squad, changes are free until the deadline"
        if wc
        else f"your FPL team already differs from the last deadline's by {moved} player(s): "
        "planned from the current team, no further transfers this week"
        if moved
        else "live team read from FPL: matches the last deadline's squad"
    )
    if active and active not in ("wildcard", "freehit"):
        note += f"; {active} is already active and is kept"
    return out, bool(moved) and not wc, note


def read_live(state: SquadState, raw: dict, rules: Rules, entry: int,
              exchange=None, http: httpx.Client | None = None):  # fmt: skip
    """Sign in once and read the current team. Returns (state, tokens, changed, note). A failed
    sign-in or read keeps `state` and says why; `tokens` is None only when sign-in failed."""
    try:
        tokens = (exchange or TokenExchange.from_env)().exchange()
    except AuthError as exc:
        return state, None, False, f"live team not read: {exc}"
    try:
        my = FPLWriter(tokens.access, entry, client=http).my_team()
    except httpx.HTTPError as exc:
        return state, tokens, False, f"live team not read ({type(exc).__name__})"
    new, changed, note = overlay(state, my, raw, rules)
    return new, tokens, changed, note
