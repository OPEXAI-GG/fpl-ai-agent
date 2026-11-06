"""One current team from public data, screenshots and corrections, with the evidence for each
field.

Every important field is a `Fact`: its value, a status, where it came from and when it was
observed. Status is one of

* confirmed: shown by a confirmed screen or stated by the manager for this gameweek, and not
  contradicted by anything newer;
* inferred: derived (public last-deadline data with this week's public transfers applied, a
  reconstructed free-transfer count, an estimated selling price, an older confirmation adjusted
  for transfers made after it);
* unknown: no source;
* conflicting: confirmed sources of the same age disagree.

No source is preferred blindly. A screen or statement only counts for the gameweek it was made
for; one made before a public transfer that it cannot know about is superseded by it; an
unconfirmed transfer basket (a Transfers screen with pending changes) never replaces the
confirmed squad. Merging is order-independent: observations are sorted before use.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from fpl_agent.data.entry import SquadState
from fpl_agent.data.players import POSITIONS
from fpl_agent.rules import Rules
from fpl_agent.solver.planner import SquadPlayer
from fpl_agent.team.observation import CHIPS, Resolved
from fpl_agent.team.public import PublicView, _ts

CONFIRMED, INFERRED, UNKNOWN, CONFLICTING = "confirmed", "inferred", "unknown", "conflicting"
FUTURE_SLACK = timedelta(minutes=10)  # clock skew allowed for an observation's timestamp


def age(t: datetime | None, now: datetime) -> str:
    if t is None:
        return "time unknown"
    s = max((now - t).total_seconds(), 0)
    if s < 120:
        return "just now"
    if s < 7200:
        return f"{s / 60:.0f} min ago"
    if s < 172800:
        return f"{s / 3600:.0f} h ago"
    return f"{s / 86400:.0f} days ago"


@dataclass
class Fact:
    value: Any
    status: str
    source: str
    observed_at: datetime | None = None
    note: str = ""
    alternatives: list[Any] = field(default_factory=list)  # the values that conflict

    def describe(self, now: datetime) -> str:
        when = f", {age(self.observed_at, now)}" if self.observed_at else ""
        return f"{self.status} ({self.source}{when})" + (f"; {self.note}" if self.note else "")

    def as_dict(self) -> dict:
        return {
            "value": self.value,
            "status": self.status,
            "source": self.source,
            "observed_at": self.observed_at.isoformat() if self.observed_at else None,
            "note": self.note,
            "alternatives": self.alternatives,
        }


@dataclass
class CurrentTeam:
    entry: int
    season: int
    gameweek: int
    cutoff: datetime  # the information cutoff: nothing observed after it is used
    squad: Fact
    lineup: Fact  # (xi, bench, captain, vice) as currently set
    bank: Fact  # tenths of £m
    free_transfers: Fact  # remaining now (after any made this gameweek)
    ft_estimate: int | None  # reconstructed count at the start of the week (never published)
    transfers_made: Fact  # transfers confirmed for this gameweek
    selling: dict[int, Fact]  # tenths of £m per squad player
    chips: dict[str, Fact]  # chip -> "available" | "used GWn" | "used" | "active" | ...
    deadline_squad: list[int] = field(default_factory=list)
    buy: dict[int, int] = field(default_factory=dict)
    unresolved: list[str] = field(default_factory=list)
    previews: list[str] = field(default_factory=list)
    stale: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    names: dict[int, str] = field(default_factory=dict, repr=False)  # FPL id -> display name

    @property
    def usable(self) -> bool:
        """A squad to plan from exists (it may still be inferred)."""
        return self.squad.value is not None and self.squad.status != CONFLICTING

    def ft_scenarios(self) -> list[int]:
        """Free-transfer balances (remaining now) to plan for: the confirmed count alone, else
        the estimate and a 1-transfer balance, plus zero unless this week's transfers are
        confirmed as none (public data shows them only after the deadline), plus any
        conflicting values. The first is primary."""
        f = self.free_transfers
        if f.status == CONFIRMED:
            return [int(f.value)]
        if f.value is None:
            return [1, 0, 2]
        out = [int(f.value), 1, *[int(v) for v in f.alternatives]]
        made = self.transfers_made
        if made.status != CONFIRMED or made.value or f.status == CONFLICTING:
            out.append(0)
        seen: list[int] = []
        for v in out:
            if v not in seen and 0 <= v <= 5:
                seen.append(v)
        return seen

    def questions(self, transfers_matter: bool) -> list[str]:
        """The specific missing pieces worth asking for (never the entry or the whole squad
        when they are known). `transfers_matter`: the advice changes with the free-transfer
        count or the bank."""
        q = []
        if self.squad.status in (UNKNOWN, CONFLICTING) or self.unresolved:
            q.append(
                "your confirmed 15: send the Pick Team screen"
                + (f" (unresolved: {'; '.join(self.unresolved)})" if self.unresolved else "")
            )
        if self.squad.status == INFERRED and not self.unresolved:
            q.append(
                "if you made transfers this gameweek, send the Pick Team screen or name them: "
                "FPL shows them publicly only after the deadline"
            )
        if transfers_matter and self.free_transfers.status != CONFIRMED:
            q.append(
                "your free-transfer count: send the Transfers screen (top shows 'Free "
                f"transfers'), or tell me the number (estimate: {self.free_transfers.value})"
            )
        if transfers_matter and self.bank.status in (UNKNOWN, CONFLICTING):
            q.append("your bank: the Transfers screen shows it as 'Budget'")
        return q

    def as_dict(self) -> dict:
        return {
            "entry": self.entry,
            "season": self.season,
            "gameweek": self.gameweek,
            "cutoff": self.cutoff.isoformat(),
            "squad": self.squad.as_dict(),
            "lineup": self.lineup.as_dict(),
            "bank": self.bank.as_dict(),
            "free_transfers": self.free_transfers.as_dict(),
            "ft_estimate": self.ft_estimate,
            "transfers_made": self.transfers_made.as_dict(),
            "selling": {str(k): v.as_dict() for k, v in sorted(self.selling.items())},
            "chips": {k: v.as_dict() for k, v in sorted(self.chips.items())},
            "deadline_squad": self.deadline_squad,
            "unresolved": self.unresolved,
            "previews": self.previews,
            "stale": self.stale,
            "notes": self.notes,
        }


def _order(r: Resolved) -> tuple:
    o = r.obs
    return (
        o.observed_at,
        o.kind,
        o.screen or "",
        json.dumps(o.model_dump(mode="json"), sort_keys=True),
    )


def _label(r: Resolved) -> str:
    o = r.obs
    return o.kind if o.kind == "correction" else f"{o.screen} screenshot"


def _latest(cands: list[tuple[datetime, str, Any]]):
    """(time, label, value, alternatives) of the newest candidate; alternatives are the other
    values observed at that same time (a conflict)."""
    if not cands:
        return None
    t = max(c[0] for c in cands)
    newest = [c for c in cands if c[0] == t]
    vals = []
    for c in newest:
        if c[2] not in vals:
            vals.append(c[2])
    return t, newest[-1][1], newest[-1][2], vals[:-1] if len(vals) > 1 else []


def merge(
    public: PublicView | None,
    observations: list[Resolved],
    *,
    entry: int,
    season: int,
    gameweek: int,
    now: datetime,
    rules: Rules,
    prices: dict[int, int] | None = None,
    names: dict[int, str] | None = None,
) -> CurrentTeam:
    """The current team for `gameweek` from every source available at `now`."""
    now = now.astimezone(UTC)
    prices = prices or {}
    names = names or {}
    nm = lambda ids: ", ".join(names.get(i, str(i)) for i in ids) or "nobody"  # noqa: E731
    stale, unresolved, previews, notes = [], [], [], []
    usable: list[Resolved] = []
    for r in sorted(observations, key=_order):
        o = r.obs
        tag = f"{_label(r)} from {age(o.observed_at, now)}"
        if o.entry != entry:
            stale.append(f"{tag}: for entry {o.entry}, not used")
        elif o.season != season or o.gameweek != gameweek:
            stale.append(f"{tag}: for GW{o.gameweek}, not GW{gameweek}; not used")
        elif o.observed_at > now + FUTURE_SLACK:
            stale.append(f"{tag}: timestamped after the information cutoff; not used")
        else:
            usable.append(r)
            unresolved += [f"{p} ({_label(r)})" for p in r.problems]
            notes += [f"{_label(r)}: {e}" for e in r.errors]

    pub_squad = public.squad if public else None
    confirmed: list[Resolved] = []
    for r in usable:
        o = r.obs
        if o.screen == "transfers":
            sq = r.complete_squad
            same = sq is not None and pub_squad is not None and sq == sorted(pub_squad)
            if o.pending is False or (o.pending is None and same and not o.transfer_cost):
                confirmed.append(r)
            else:
                previews.append(_preview_text(r, pub_squad, now, nm))
        else:
            confirmed.append(r)

    # --- squad --------------------------------------------------------------------------
    sq_cands = [
        (r.obs.observed_at, _label(r), r.complete_squad)
        for r in confirmed
        if r.complete_squad is not None and r.obs.screen in ("pick_team", "transfers", None)
    ]
    best = _latest(sq_cands)
    if best and public and (later := public.transfers_after(best[0])):
        squad = Fact(
            sorted(public.squad),
            INFERRED,
            "public data",
            public.as_of,
            f"{len(later)} public transfer(s) after the {best[1]} of {age(best[0], now)}",
        )
    elif best and best[3]:
        squad = Fact(
            best[2], CONFLICTING, best[1], best[0], "two confirmed sources disagree", best[3]
        )
    elif best:
        agree = public is not None and best[2] == sorted(public.squad)
        squad = Fact(best[2], CONFIRMED, best[1] + (" + public data" if agree else ""), best[0])
        if public and not agree:
            diff_in = sorted(set(best[2]) - set(public.squad))
            diff_out = sorted(set(public.squad) - set(best[2]))
            squad.note = (
                f"public data still shows {nm(diff_out)} instead of {nm(diff_in)}: the newer "
                f"{best[1]} is used"
            )
    elif public:
        n = len(public.transfers)
        squad = Fact(
            sorted(public.squad),
            INFERRED,
            "public data",
            public.as_of,
            "last deadline's picks" + (f" + {n} transfer(s) made since" if n else ""),
        )
    else:
        squad = Fact(None, UNKNOWN, "none")
    owned = set(squad.value or [])

    # --- transfers already made this gameweek -------------------------------------------
    stated = _latest(
        [
            (r.obs.observed_at, _label(r), r.obs.transfers_made)
            for r in confirmed
            if r.obs.transfers_made is not None
        ]
    )
    base = public.deadline_squad if public else []
    if stated:
        made = Fact(
            stated[2],
            CONFLICTING if stated[3] else CONFIRMED,
            stated[1],
            stated[0],
            alternatives=stated[3],
        )
    elif squad.status == CONFIRMED and len(base) == 15:
        made = Fact(
            len(owned - set(base)),
            CONFIRMED,
            squad.source,
            squad.observed_at,
            "confirmed squad compared with the last deadline's",
        )
    elif public:
        made = Fact(
            len(public.transfers),
            INFERRED,
            "public transfer list",
            public.fetched_at,
            "FPL lists this gameweek's transfers publicly only after the deadline",
        )
    else:
        made = Fact(None, UNKNOWN, "none")

    # --- free transfers (remaining) and bank -------------------------------------------
    ft = _with_later(
        confirmed, "free_transfers", public, now, lambda v, later: max(0, v - len(later))
    )
    if ft is None:
        if public:
            used = len(public.transfers)
            ft = Fact(
                max(0, public.ft_estimate - used),
                INFERRED,
                "reconstructed from history",
                public.fetched_at,
                "FPL does not publish the count; estimate"
                + (f" less {used} transfer(s) made this week" if used else ""),
            )
        else:
            ft = Fact(None, UNKNOWN, "none")
    elif (
        public
        and ft.status == CONFIRMED
        and ft.value != max(0, public.ft_estimate - len(public.transfers))
    ):
        ft.note = f"history reconstruction said {public.ft_estimate}" + (
            " before this week's transfers" if public.transfers else ""
        )

    def bank_after(v: int, later: list[dict]) -> int:
        for t in later:
            v += (public.sold_for.get(t["element_out"], 0) if public else 0) - int(
                t["element_in_cost"]
            )
        return v

    bank = _with_later(confirmed, "bank", public, now, bank_after)
    if bank is None:
        if public:
            n = len(public.transfers)
            bank = Fact(
                public.bank,
                INFERRED,
                "public data",
                public.as_of,
                "last deadline's bank" + (f" adjusted for {n} transfer(s)" if n else ""),
            )
        else:
            bank = Fact(None, UNKNOWN, "none")

    # --- selling prices -------------------------------------------------------------------
    selling: dict[int, Fact] = {}
    shown = {}
    for r in usable:  # sorted: later screens overwrite earlier ones
        if r.obs.screen == "transfers" or r.obs.kind == "correction":
            for p, e in zip(r.obs.players, r.elements, strict=True):
                if e is not None and p.price is not None:
                    shown[e] = (p.price, r)
    for e in sorted(owned):
        if e in shown and not (
            public and e in public.sell and _bought_after(public, e, shown[e][1])
        ):
            price, r = shown[e]
            selling[e] = Fact(price, CONFIRMED, _label(r), r.obs.observed_at)
            if public and e in public.sell and public.sell[e] != price:
                selling[e].note = f"estimate was {public.sell[e]}"
        elif public and e in public.sell:
            selling[e] = Fact(
                public.sell[e], INFERRED, "estimated from purchase price", public.fetched_at
            )
        else:
            selling[e] = Fact(prices.get(e), UNKNOWN, "current price used as a stand-in")

    # --- lineup ---------------------------------------------------------------------------
    lu = [
        (r.obs.observed_at, _label(r), r.lineup())
        for r in confirmed
        if r.obs.screen == "pick_team" and r.lineup() is not None
    ]
    lu = [c for c in lu if sorted(c[2][0] + c[2][1]) == sorted(owned)]
    newest = _latest(lu)
    if newest:
        lineup = Fact(
            newest[2],
            CONFLICTING if newest[3] else CONFIRMED,
            newest[1],
            newest[0],
            alternatives=newest[3],
        )
    elif public and public.deadline_lineup and sorted(public.deadline_squad) == sorted(owned):
        lineup = Fact(
            public.deadline_lineup,
            INFERRED,
            "public data",
            public.deadline_time,
            "the last deadline's lineup",
        )
    else:
        lineup = Fact(None, UNKNOWN, "none")

    chips = _chips(public, confirmed, gameweek, rules)
    return CurrentTeam(
        entry=entry,
        season=season,
        gameweek=gameweek,
        cutoff=now,
        squad=squad,
        lineup=lineup,
        bank=bank,
        free_transfers=ft,
        ft_estimate=public.ft_estimate if public else None,
        transfers_made=made,
        selling=selling,
        chips=chips,
        deadline_squad=sorted(base),
        buy=dict(public.buy) if public else {},
        unresolved=unresolved,
        previews=previews,
        stale=stale,
        notes=notes,
        names=names,
    )


def _bought_after(public: PublicView, element: int, r: Resolved) -> bool:
    """The player was bought (again) after the screen was taken: its price there is old."""
    return any(t["element_in"] == element for t in public.transfers_after(r.obs.observed_at))


def _with_later(
    confirmed: list[Resolved], name: str, public: PublicView | None, now: datetime, adjust
) -> Fact | None:
    """The newest confirmed value of a field, adjusted (and then only inferred) for public
    transfers made after it was observed."""
    best = _latest(
        [
            (r.obs.observed_at, _label(r), getattr(r.obs, name))
            for r in confirmed
            if getattr(r.obs, name) is not None
        ]
    )
    if best is None:
        return None
    t, label, v, alts = best
    if alts:
        return Fact(v, CONFLICTING, label, t, "sources of the same time disagree", alts)
    later = public.transfers_after(t) if public else []
    if later:
        return Fact(
            adjust(v, later),
            INFERRED,
            label,
            t,
            f"adjusted for {len(later)} public transfer(s) made after it",
        )
    return Fact(v, CONFIRMED, label, t)


def _chips(
    public: PublicView | None, confirmed: list[Resolved], gw: int, rules: Rules
) -> dict[str, Fact]:
    used = public.chips_used if public else []
    out: dict[str, Fact] = {}
    for chip in CHIPS:
        window = next(((lo, hi) for lo, hi in rules["chips"].get(chip, []) if lo <= gw <= hi), None)
        if window is None:
            out[chip] = Fact("not in a window", CONFIRMED, "rules")
            continue
        played = [ev for c, ev in used if c == chip and window[0] <= ev <= window[1]]
        if played:
            out[chip] = Fact(
                f"used GW{played[-1]}",
                CONFIRMED,
                "public history",
                public.fetched_at if public else None,
            )
        elif public:
            out[chip] = Fact(
                "available", INFERRED, "public history and the chip windows", public.fetched_at
            )
        else:
            out[chip] = Fact(None, UNKNOWN, "none")
    for r in confirmed:  # sorted oldest first: the newest statement stands
        for chip, state in r.obs.chips.items():
            pub = out.get(chip)
            if state == "available" and pub and str(pub.value).startswith("used"):
                out[chip] = Fact(
                    state,
                    CONFLICTING,
                    _label(r),
                    r.obs.observed_at,
                    f"public history says {pub.value}",
                    [pub.value],
                )
            elif state == "used" and pub and str(pub.value).startswith("used"):
                out[chip] = Fact(
                    pub.value, CONFIRMED, f"{_label(r)} + public history", r.obs.observed_at
                )
            else:
                out[chip] = Fact(state, CONFIRMED, _label(r), r.obs.observed_at)
    return out


def _preview_text(r: Resolved, pub_squad: list[int] | None, now: datetime, nm) -> str:
    o = r.obs
    sq = [e for e in r.elements if e is not None]
    what = ""
    if pub_squad is not None and sq:
        ins, outs = sorted(set(sq) - set(pub_squad)), sorted(set(pub_squad) - set(sq))
        if ins or outs:
            what = f": out {nm(outs)}, in {nm(ins)}"
    why = (
        "pending changes shown"
        if o.pending
        else f"cost -{o.transfer_cost} shown"
        if o.transfer_cost
        else "pending state unclear and it differs from the public squad"
    )
    return (
        f"unconfirmed transfer basket on the Transfers screen from {age(o.observed_at, now)} "
        f"({why}){what}; not used as your squad"
    )


def to_state(
    team: CurrentTeam, bootstrap: dict, rules: Rules, ft_remaining: int | None = None
) -> SquadState:
    """The planner's squad state (FPL ids) for one free-transfer balance (remaining now)."""
    if not team.usable:
        raise ValueError("no usable squad: " + team.squad.describe(team.cutoff))
    els = {e["id"]: e for e in bootstrap["elements"]}
    squad = []
    for e in team.squad.value:
        el = els[e]
        sell = team.selling[e].value if team.selling.get(e) else None
        sell = int(sell if sell is not None else el["now_cost"])
        squad.append(
            SquadPlayer(
                e, POSITIONS[el["element_type"]], el["team"], int(team.buy.get(e, sell)), sell
            )
        )
    base = [
        SquadPlayer(e, POSITIONS[els[e]["element_type"]], els[e]["team"], 0, 0)
        for e in team.deadline_squad
        if e in els
    ]
    made = int(team.transfers_made.value or 0)
    remaining = ft_remaining if ft_remaining is not None else int(team.free_transfers.value or 0)
    used, wc = [], False
    for chip, f in team.chips.items():
        v = str(f.value)
        if v.startswith("used GW"):
            used.append((chip, int(v[len("used GW") :])))
        elif v in ("used", "unavailable"):
            used.append((chip, team.gameweek - 1))
        elif v == "active":
            wc = wc or chip == "wildcard"
            if chip != "wildcard":
                used.append((chip, team.gameweek))
    return SquadState(
        next_gw=team.gameweek,
        squad=squad,
        bank=int(team.bank.value or 0),
        # the planner's count is the bank at the start of the week; it subtracts this week's
        free_transfers=remaining + made,
        chips_used=used,
        already_transferred=0 if wc else made,
        wildcard_active=wc,
        base_squad=base,
        # dry analysis of further moves: only when the squad and this week's completed moves
        # are confirmed. The remaining count is used as is (never reduced by them again).
        remaining_free_transfers=(
            remaining
            if made
            and not wc
            and team.squad.status == CONFIRMED
            and team.transfers_made.status == CONFIRMED
            else None
        ),
    )


def load_time(s: str | None) -> datetime | None:
    return _ts(s)


__all__ = [
    "Fact",
    "CurrentTeam",
    "merge",
    "to_state",
    "age",
    "CONFIRMED",
    "INFERRED",
    "UNKNOWN",
    "CONFLICTING",
]
