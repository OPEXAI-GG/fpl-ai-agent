"""Authenticated FPL calls: transfers (preview, then confirm), lineup, and read-back.

Only `execute.py` constructs this, and only in live mode. Every payload is built from the
decision by pure functions that are tested without a network.
"""

from __future__ import annotations

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
                      price: dict[int, int], confirmed: bool,
                      position: dict[int, str] | None = None) -> dict[str, Any]:  # fmt: skip
    sell = {p.player_id: p.sell_price for p in state.squad}
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
    if position:  # FPL wants the starting keeper in slot 1 and the spare keeper in slot 12
        xi.sort(key=lambda p: position.get(p) != "GKP")
        bench.sort(key=lambda p: position.get(p) != "GKP")
    order = xi + bench
    # slots 1..15: starting XI, then the bench in priority order
    picks = [{"element": fpl_id[p], "position": n + 1, "is_captain": p == d.captain,
              "is_vice_captain": p == d.vice} for n, p in enumerate(order)]  # fmt: skip
    chip = d.chip if d.chip in ("bboost", "3xc") else None
    return {"chip": chip, "picks": picks}


def preview_matches(preview: dict[str, Any], d: Decision, fpl_id: dict[int, int]) -> list[str]:
    """Differences between FPL's preview response and our plan (empty = they agree)."""
    out = []
    want_in = sorted(fpl_id[i] for i in d.transfers_in)
    got_in = sorted(t["element_in"] for t in preview.get("transfers", []))
    if want_in != got_in:
        out.append(f"transfers in: planned {want_in}, preview {got_in}")
    cost = preview.get("points_cost", preview.get("event_transfers_cost"))
    if cost is not None and int(cost) != 4 * d.hits:
        out.append(f"points cost: planned {4 * d.hits}, preview {cost}")
    bank = preview.get("bank")
    if bank is not None and int(bank) != d.bank_after:
        out.append(f"bank: planned {d.bank_after}, preview {bank}")
    return out


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
