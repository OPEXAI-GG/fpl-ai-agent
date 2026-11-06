"""What a manager's screenshot or correction says, as a validated structure.

Extraction (reading an image) is separate from this module: whoever reads the screenshot, in
practice Claude reading an attached image, writes what is visible into this schema and
submits it (`fpl-agent team add`). This module checks the structure, converts money to integer
tenths of £m, and resolves each visible player to an FPL element using name, position, club and
the fixture shown. A name that does not resolve to exactly one player stays unresolved; it is
never guessed.

Screens:
* `pick_team`: the confirmed 15 with XI, ordered bench, captain and vice (no pending transfers
  can be shown there);
* `transfers`: the 15 with selling prices, Budget (bank), Free transfers and Cost; may show an
  unconfirmed transfer basket (`pending`);
* `chips`: chip states only.
A `correction` is the manager's own statement of any of these fields.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, field_validator, model_validator

POSITIONS = ("GKP", "DEF", "MID", "FWD")
POS_ALIASES = {
    "GK": "GKP",
    "GKP": "GKP",
    "GOALKEEPER": "GKP",
    "DEF": "DEF",
    "DEFENDER": "DEF",
    "MID": "MID",
    "MIDFIELDER": "MID",
    "FWD": "FWD",
    "FORWARD": "FWD",
}
CHIPS = ("wildcard", "freehit", "bboost", "3xc")
CHIP_ALIASES = {
    "wildcard": "wildcard",
    "wc": "wildcard",
    "freehit": "freehit",
    "free hit": "freehit",
    "fh": "freehit",
    "bboost": "bboost",
    "bench boost": "bboost",
    "bb": "bboost",
    "benchboost": "bboost",
    "3xc": "3xc",
    "triple captain": "3xc",
    "tc": "3xc",
    "triplecaptain": "3xc",
}
ChipState = Literal["available", "used", "active", "unavailable"]
SQUAD_SHAPE = {"GKP": 2, "DEF": 5, "MID": 5, "FWD": 3}


def tenths(v: Any) -> int | None:
    """Money -> integer tenths of £m. An int is already tenths (2 = £0.2m); a float or a string
    is £m as shown on screen (0.2, '0.2', '£0.2m' -> 2)."""
    if v is None or v == "":
        return None
    if isinstance(v, bool):
        raise ValueError("money must be a number")
    if isinstance(v, int):
        return v
    if isinstance(v, float):
        return round(v * 10)
    m = re.fullmatch(r"\s*£?\s*(-?\d+(?:\.\d+)?)\s*m?\s*", str(v), flags=re.I)
    if not m:
        raise ValueError(f"not a money amount: {v!r}")
    return round(float(m.group(1)) * 10)


class SeenPlayer(BaseModel):
    """One player card as shown."""

    model_config = ConfigDict(extra="forbid")

    name: str
    position: str | None = None  # GKP/DEF/MID/FWD (the pitch row)
    club: str | None = None  # short name when shown, e.g. ARS
    opponent: str | None = None  # the fixture text, e.g. "HUL (H)"; several separated by ","
    price: int | None = None  # tenths of £m; on the Transfers screen the selling price
    slot: Literal["xi", "bench"] | None = None
    bench_order: int | None = None  # 0 = bench goalkeeper, 1..3 = outfield order
    captain: bool = False
    vice: bool = False
    element: int | None = None  # the FPL id when the submitter already knows it

    @field_validator("position")
    @classmethod
    def _pos(cls, v):
        if v is None:
            return v
        p = POS_ALIASES.get(v.strip().upper())
        if not p:
            raise ValueError(f"unknown position {v!r}")
        return p

    @field_validator("price", mode="before")
    @classmethod
    def _price(cls, v):
        return tenths(v)


class Observation(BaseModel):
    """One screenshot's (or one correction's) content. Money is stored in tenths of £m."""

    model_config = ConfigDict(extra="forbid")

    kind: Literal["screenshot", "correction"]
    screen: Literal["pick_team", "transfers", "chips", "other"] | None = None
    entry: int
    season: int = 2026
    gameweek: int  # the upcoming gameweek the screen is for (its deadline)
    observed_at: datetime  # when the screen was captured or the statement made
    pending: bool | None = None  # transfers screen: unconfirmed changes shown? None = unclear
    players: list[SeenPlayer] = []
    bank: int | None = None
    free_transfers: int | None = None
    transfer_cost: int | None = None  # points shown as Cost
    transfers_made: int | None = None  # transfers already confirmed for this gameweek
    chips: dict[str, ChipState] = {}
    source: str = ""  # e.g. the uploaded file's name; never the image itself
    note: str = ""

    @field_validator("bank", mode="before")
    @classmethod
    def _bank(cls, v):
        return tenths(v)

    @field_validator("observed_at")
    @classmethod
    def _utc(cls, v: datetime):
        return v.replace(tzinfo=UTC) if v.tzinfo is None else v.astimezone(UTC)

    @field_validator("chips", mode="before")
    @classmethod
    def _chips(cls, v):
        out = {}
        for k, state in (v or {}).items():
            name = CHIP_ALIASES.get(str(k).strip().lower())
            if not name:
                raise ValueError(f"unknown chip {k!r}")
            out[name] = state
        return out

    @model_validator(mode="after")
    def _check(self):
        if self.free_transfers is not None and not 0 <= self.free_transfers <= 5:
            raise ValueError("free_transfers must be 0-5")
        if self.bank is not None and self.bank < 0:
            raise ValueError("bank cannot be negative")
        if self.kind == "screenshot" and self.screen is None:
            raise ValueError("a screenshot needs its screen: pick_team, transfers, chips, other")
        if self.screen == "pick_team" and self.pending:
            raise ValueError("the Pick Team screen cannot show pending transfers")
        caps = sum(p.captain for p in self.players)
        vices = sum(p.vice for p in self.players)
        if caps > 1 or vices > 1:
            raise ValueError("at most one captain and one vice")
        return self


@dataclass
class Resolved:
    """An observation with every visible player resolved to an FPL element, or not."""

    obs: Observation
    elements: list[int | None]  # per player, in the order given; None = unresolved
    problems: list[str] = field(default_factory=list)  # unresolved or ambiguous names
    errors: list[str] = field(default_factory=list)  # structural problems (wrong squad shape)

    @property
    def complete_squad(self) -> list[int] | None:
        """The 15 elements, when all 15 resolved and the squad is legal; else None."""
        if len(self.elements) != 15 or None in self.elements or self.errors:
            return None
        return sorted(e for e in self.elements if e is not None)

    def player(self, element: int) -> SeenPlayer | None:
        for p, e in zip(self.obs.players, self.elements, strict=True):
            if e == element:
                return p
        return None

    def lineup(self) -> tuple[list[int], list[int], int | None, int | None] | None:
        """(xi, bench in order, captain, vice) from a Pick Team screen, when complete."""
        if self.complete_squad is None or self.obs.screen not in ("pick_team", None):
            return None
        rows = list(zip(self.obs.players, self.elements, strict=True))
        if any(p.slot is None for p, _ in rows):
            return None
        xi = sorted(e for p, e in rows if p.slot == "xi")
        bench = [
            e
            for p, e in sorted(
                ((p, e) for p, e in rows if p.slot == "bench"),
                key=lambda r: (r[0].bench_order is None, r[0].bench_order or 0),
            )
        ]
        if len(xi) != 11 or len(bench) != 4:
            return None
        cap = next((e for p, e in rows if p.captain), None)
        vice = next((e for p, e in rows if p.vice), None)
        return xi, bench, cap, vice


def norm(s: str) -> str:
    """Accent-, case- and punctuation-insensitive form of a name ('E.Le Fée' -> 'eleféé'...)."""
    s = unicodedata.normalize("NFKD", s.casefold())
    s = "".join(c for c in s if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9]", "", s)


def _names(e: dict) -> set[str]:
    first, second, web = e.get("first_name", ""), e.get("second_name", ""), e.get("web_name", "")
    out = {norm(web), norm(second), norm(f"{first} {second}")}
    if first:
        out.add(norm(f"{first[0]}{second}"))  # "E.Le Fée" style initials
    return {n for n in out if n}


def _clubs_from_opponent(text: str, gw: int, fixtures: list[dict], teams: dict[str, int]):
    """Clubs that play the shown fixture(s) in `gw`: 'HUL (H)' means at home against Hull."""
    found: set[int] | None = None
    for part in [p.strip() for p in text.split(",") if p.strip()]:
        m = re.fullmatch(r"([A-Za-z]{3})\s*\(([HA])\)", part)
        if not m or m.group(1).upper() not in teams:
            return None  # unreadable fixture text: no club evidence
        opp, home = teams[m.group(1).upper()], m.group(2).upper() == "H"
        clubs = {
            f["team_h"] if home else f["team_a"]
            for f in fixtures
            if f.get("event") == gw and (f["team_a"] if home else f["team_h"]) == opp
        }
        found = clubs if found is None else found & clubs
    return found


def resolve(obs: Observation, bootstrap: dict, fixtures: list[dict]) -> Resolved:
    """Resolve each player to exactly one element using name, position, club and fixture."""
    els = bootstrap["elements"]
    teams = {t["short_name"].upper(): t["id"] for t in bootstrap.get("teams", [])}
    pos_of = {1: "GKP", 2: "DEF", 3: "MID", 4: "FWD"}
    by_id = {e["id"]: e for e in els}
    out: list[int | None] = []
    problems: list[str] = []
    for p in obs.players:
        if p.element is not None:
            if p.element not in by_id:
                problems.append(f"{p.name}: element {p.element} does not exist")
                out.append(None)
            else:
                out.append(p.element)
            continue
        key = norm(p.name)
        cands = [e for e in els if key and key in _names(e)]
        if p.position:
            cands = [e for e in cands if pos_of[e["element_type"]] == p.position]
        if p.club:
            club = teams.get(p.club.strip().upper())
            cands = [e for e in cands if e["team"] == club]
        if p.opponent and len(cands) > 1:
            clubs = _clubs_from_opponent(p.opponent, obs.gameweek, fixtures, teams)
            if clubs is not None:
                cands = [e for e in cands if e["team"] in clubs]
        if len(cands) == 1:
            out.append(cands[0]["id"])
            continue
        out.append(None)
        if not cands:
            problems.append(
                f"{p.name}: no player matches"
                + (
                    f" ({p.position}" + (f", {p.club}" if p.club else "") + ")"
                    if p.position
                    else ""
                )
            )
        else:
            short = {t["id"]: t["short_name"] for t in bootstrap.get("teams", [])}
            opts = ", ".join(
                f"{e.get('web_name', e['id'])} ({short.get(e['team'], e['team'])}, id {e['id']})"
                for e in cands[:6]
            )
            problems.append(f"{p.name}: ambiguous, could be {opts}")
    errors = _shape_errors(obs, out, by_id, pos_of)
    return Resolved(obs, out, problems, errors)


def _shape_errors(obs: Observation, elements: list[int | None], by_id: dict, pos_of) -> list[str]:
    if obs.screen not in ("pick_team", "transfers") and not (
        obs.kind == "correction" and len(obs.players) == 15
    ):
        return []
    errs = []
    if len(obs.players) != 15:
        errs.append(f"{len(obs.players)} players shown, a squad has 15")
        return errs
    known = [by_id[e] for e in elements if e is not None]
    if len({e["id"] for e in known}) != len(known):
        errs.append("the same player appears twice")
    counts: dict[str, int] = {}
    clubs: dict[int, int] = {}
    for e in known:
        counts[pos_of[e["element_type"]]] = counts.get(pos_of[e["element_type"]], 0) + 1
        clubs[e["team"]] = clubs.get(e["team"], 0) + 1
    if None not in elements and counts != SQUAD_SHAPE:
        errs.append(f"positions {counts} are not 2 GKP, 5 DEF, 5 MID, 3 FWD")
    if any(n > 3 for n in clubs.values()):
        errs.append("more than three players from one club")
    for p, e in zip(obs.players, elements, strict=True):
        if e is not None and p.price is not None and obs.screen == "transfers":
            now = by_id[e]["now_cost"]
            if p.price > now:
                errs.append(
                    f"{p.name}: shown £{p.price / 10:.1f}m is above the current price "
                    f"£{now / 10:.1f}m, so it is not a selling price"
                )
    return errs


def now_utc() -> datetime:
    return datetime.now(UTC)


__all__ = ["Observation", "SeenPlayer", "Resolved", "resolve", "tenths", "norm", "CHIPS"]
