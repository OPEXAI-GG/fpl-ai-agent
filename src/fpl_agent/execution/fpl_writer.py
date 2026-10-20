"""Authenticated FPL calls: transfers, lineup, and read-back.

Only `execute.py` constructs this, and only in live mode. Every payload is built from the
decision by pure functions that are tested without a network.

The transfers call is treated as binding: there is no "preview" step (a POST with
`confirmed: false` has been reported to apply transfers too). The my-team fields read here
(`picks[].element/position/selling_price/is_captain/is_vice_captain`, `transfers.bank/limit/made`,
`chips[].name/status_for_entry`) follow FPL's public web app; they have not been pinned to a
captured response from this account, so any that are missing are reported as unverified rather
than assumed.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import httpx

from fpl_agent.config import FPL_BASE_URL
from fpl_agent.data.entry import SquadState
from fpl_agent.solver.decide import Decision


def paired_transfers(d: Decision, position: dict[int, str] | None) -> list[tuple[int, int]]:
    """(player in, player out) pairs. FPL only accepts a swap of the same position, so each
    player coming in is matched with a player going out in his position (the plan itself only
    lists who comes in and who goes out)."""
    if position is None:
        return list(zip(d.transfers_in, d.transfers_out, strict=True))
    leaving: dict[str, list[int]] = {}
    for o in d.transfers_out:
        leaving.setdefault(position[o], []).append(o)
    pairs = []
    for i in d.transfers_in:
        group = leaving.get(position[i])
        if not group:
            raise ValueError(f"no player leaving in position {position[i]} for {d.name(i)}")
        pairs.append((i, group.pop(0)))
    return pairs


def transfers_payload(d: Decision, state: SquadState, entry: int, fpl_id: dict[int, int],
                      price: dict[int, int], confirmed: bool = True,
                      position: dict[int, str] | None = None,
                      sell: dict[int, int] | None = None) -> dict[str, Any]:  # fmt: skip
    """`sell` (canonical id -> selling price) overrides the state's prices, e.g. with the ones
    FPL's current team reports."""
    sell = {p.player_id: p.sell_price for p in state.squad} | (sell or {})
    return {
        "chip": d.chip if d.chip in ("wildcard", "freehit") else None,
        "entry": entry, "event": d.gw, "confirmed": confirmed,
        "transfers": [
            {"element_in": fpl_id[i], "element_out": fpl_id[o],
             "purchase_price": price[i], "selling_price": sell[o]}
            for i, o in paired_transfers(d, position)
        ],
    }  # fmt: skip


def lineup_payload(d: Decision, fpl_id: dict[int, int],
                   position: dict[int, str] | None = None) -> dict[str, Any]:  # fmt: skip
    xi, bench = list(d.xi), list(d.bench)
    if (
        position
    ):  # FPL wants the XI ordered keeper, defenders, midfielders, forwards; spare keeper 12th
        kind = {"GKP": 0, "DEF": 1, "MID": 2, "FWD": 3}
        xi.sort(
            key=lambda p: kind.get(position.get(p, ""), 4)
        )  # stable: keeps our order within a line
        bench.sort(key=lambda p: position.get(p) != "GKP")
    order = xi + bench
    # slots 1..15: starting XI, then the bench in priority order
    picks = [{"element": fpl_id[p], "position": n + 1, "is_captain": p == d.captain,
              "is_vice_captain": p == d.vice} for n, p in enumerate(order)]  # fmt: skip
    chip = d.chip if d.chip in ("bboost", "3xc") else None
    return {"chip": chip, "picks": picks}


def team_squad(team: dict[str, Any]) -> set[int]:
    return {p["element"] for p in team.get("picks") or [] if "element" in p}


def _int(v: Any) -> int | None:
    return v if isinstance(v, int) and not isinstance(v, bool) else None


def active_chips(team: dict[str, Any]) -> set[str] | None:
    """Chips FPL says are active for the coming deadline; None when the reply has no chip list."""
    chips = team.get("chips")
    if not isinstance(chips, list):
        return None
    return {c.get("name") for c in chips if c.get("status_for_entry") == "active"}


@dataclass
class Preflight:
    problems: list[str]  # any problem: send nothing
    unverified: list[str]  # what the reply did not let us check
    sell: dict[int, int]  # FPL element id -> selling price FPL reports
    bank_after: int | None  # bank once the transfers are made, from FPL's own figures


def preflight(team: dict[str, Any], d: Decision, fpl_id: dict[int, int],
              price: dict[int, int]) -> Preflight:  # fmt: skip
    """Check the planned transfers against the team FPL holds right now, before anything is
    sent: the players going out are owned and the ones coming in are not, the bank stays
    non-negative and matches the plan, the points cost matches, and no other chip is active."""
    problems, unverified = [], []
    picks = team.get("picks") or []
    held = team_squad(team)
    if len(picks) != 15 or len(held) != 15:
        return Preflight([f"current team has {len(held)} players, not 15"], [], {}, None)
    want_in = [fpl_id[i] for i in d.transfers_in]
    want_out = [fpl_id[o] for o in d.transfers_out]
    if missing := [o for o in want_out if o not in held]:
        problems.append(f"players to sell are not in the current team: {missing}")
    if owned := [i for i in want_in if i in held]:
        problems.append(f"players to buy are already in the current team: {owned}")
    sell = {p["element"]: s for p in picks if (s := _int(p.get("selling_price"))) is not None}
    if len(sell) != len(picks):
        unverified.append("selling prices (planned ones used)")
    t = team.get("transfers") if isinstance(team.get("transfers"), dict) else {}
    bank, bank_after = _int(t.get("bank")), None
    if bank is None:
        unverified.append("bank")
    elif not problems and all(o in sell for o in want_out):
        bank_after = bank + sum(sell[o] for o in want_out) - sum(price[i] for i in d.transfers_in)
        if bank_after < 0:
            problems.append(f"bank would be {bank_after} after the transfers")
        elif bank_after != d.bank_after:
            problems.append(f"bank after: planned {d.bank_after}, FPL figures {bank_after}")
    limit, made = _int(t.get("limit")), _int(t.get("made"))
    if d.chip in ("wildcard", "freehit"):
        pass  # a Wildcard or Free Hit makes every change free
    elif limit is None or made is None:
        unverified.append("points cost")
    else:
        cost = 4 * max(0, made + len(want_in) - limit)
        if cost != 4 * d.hits:
            problems.append(f"points cost: planned {4 * d.hits}, FPL figures {cost}")
    active = active_chips(team)
    if active is None:
        unverified.append("active chip")
    elif active - {d.chip}:
        problems.append(f"chip {sorted(active)} already active; plan uses {d.chip or 'none'}")
    return Preflight(problems, unverified, sell, bank_after)


def readback_diff(team: dict[str, Any], squad: set[int], lineup: dict[str, Any] | None,
                  chip: str | None, bank: int | None) -> tuple[list[str], list[str]]:  # fmt: skip
    """(differences, unverifiable) between the team FPL reports and what should now be true:
    the exact squad; with a lineup sent, the XI, the bench slots in order, captain and vice;
    the active chip and the bank where FPL reports them."""
    diffs, unverified = [], []
    picks = team.get("picks") or []
    got = team_squad(team)
    if got != squad:
        diffs.append(f"squad: missing {sorted(squad - got)}, unexpected {sorted(got - squad)}")
    if lineup is not None:
        slot = {p.get("position"): p.get("element") for p in picks}
        want = {p["position"]: p["element"] for p in lineup["picks"]}
        if {slot.get(n) for n in range(1, 12)} != {want[n] for n in range(1, 12)}:
            diffs.append("starting XI differs")
        if [slot.get(n) for n in range(12, 16)] != [want[n] for n in range(12, 16)]:
            diffs.append("bench order differs")
        for flag, what in (("is_captain", "captain"), ("is_vice_captain", "vice-captain")):
            w = [p["element"] for p in lineup["picks"] if p[flag]]
            g = [p.get("element") for p in picks if p.get(flag)]
            if w != g:
                diffs.append(f"{what}: planned {w}, FPL has {g}")
    active = active_chips(team)
    if active is None:
        unverified.append("active chip")
    elif chip and chip not in active:
        diffs.append(f"chip {chip} is not active")
    t = team.get("transfers") if isinstance(team.get("transfers"), dict) else {}
    if bank is not None:
        if _int(t.get("bank")) is None:
            unverified.append("bank")
        elif t["bank"] != bank:
            diffs.append(f"bank: expected {bank}, FPL has {t['bank']}")
    return diffs, unverified


class FPLWriter:
    def __init__(self, access_token: str, entry: int, base_url: str = FPL_BASE_URL,
                 client: httpx.Client | None = None) -> None:  # fmt: skip
        self.entry = entry
        self._base = base_url.rstrip("/")
        self._http = client or httpx.Client(timeout=30)
        self._headers = {"X-API-Authorization": f"Bearer {access_token}",
                         "User-Agent": "fpl-agent/0.1"}  # fmt: skip

    def _req(self, method: str, path: str, **kw: Any) -> Any:
        r = self._http.request(method, f"{self._base}/{path.lstrip('/')}", headers=self._headers,
                               **kw)  # fmt: skip
        r.raise_for_status()
        return r.json() if r.content else {}

    def my_team(self) -> dict[str, Any]:
        return self._req("GET", f"my-team/{self.entry}/")

    def transfers(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self._req("POST", "transfers/", json=payload)

    def set_lineup(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self._req("POST", f"my-team/{self.entry}/", json=payload)
