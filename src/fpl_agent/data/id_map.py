"""Map Understat players onto FPL players.

Every source names players differently ("Raya Martín" vs "David Raya"), so matching is
done inside the club, in order of confidence: exact full name, FPL web_name, token overlap,
then fuzzy ratio. Hand-made fixes live in data/id_overrides.csv and always win.
Unmatched players are returned, never silently dropped, so coverage is visible.
"""

from __future__ import annotations

import csv
import html
import re
import unicodedata
from dataclasses import dataclass
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any

import pandas as pd

OVERRIDES_PATH = Path(__file__).with_name("id_overrides.csv")

# Understat club title -> FPL short_name (only where a name can't be matched normally)
TEAM_ALIASES = {
    "manchester city": "man city",
    "manchester united": "man utd",
    "newcastle united": "newcastle",
    "wolverhampton wanderers": "wolves",
    "tottenham": "spurs",
    "nottingham forest": "nott'm forest",
    "west ham": "west ham",
}

FUZZY_THRESHOLD = 0.86


def normalise(name: str) -> str:
    s = unicodedata.normalize("NFKD", html.unescape(name))
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = s.lower().replace("ø", "o").replace("ß", "ss").replace("æ", "ae").replace("đ", "d")
    s = re.sub(r"[^a-z0-9 ]+", " ", s.replace("-", " "))
    return re.sub(r"\s+", " ", s).strip()


def _tokens(name: str) -> set[str]:
    return set(normalise(name).split())


@dataclass(frozen=True)
class Match:
    understat_id: int
    fpl_id: int | None
    method: str
    score: float


def team_key(title: str) -> str:
    """Understat lists multi-club players as "A,B" (latest last): use the latest club."""
    last = title.split(",")[-1].strip()
    key = normalise(last)
    return TEAM_ALIASES.get(key, key)


def load_overrides(path: Path = OVERRIDES_PATH) -> dict[int, int]:
    if not path.exists():
        return {}
    with path.open(encoding="utf-8") as fh:
        return {int(r["understat_id"]): int(r["fpl_id"]) for r in csv.DictReader(fh)}


def build_id_map(
    fpl_bootstrap: dict[str, Any],
    understat_players: list[dict[str, Any]],
    overrides: dict[int, int] | None = None,
) -> pd.DataFrame:
    """Return one row per Understat player: understat_id, fpl_id (nullable), method, score."""
    overrides = load_overrides() if overrides is None else overrides
    teams = {t["id"]: normalise(t["short_name"]) for t in fpl_bootstrap["teams"]}
    names = {t["id"]: normalise(t["name"]) for t in fpl_bootstrap["teams"]}
    by_team: dict[str, list[dict[str, Any]]] = {}
    for el in fpl_bootstrap["elements"]:
        for key in {teams[el["team"]], names[el["team"]]}:
            by_team.setdefault(key, []).append(el)

    taken: set[int] = set()
    results: list[Match] = []
    pending = []
    for up in understat_players:
        uid = int(up["id"])
        if uid in overrides:
            taken.add(overrides[uid])
            results.append(Match(uid, overrides[uid], "override", 1.0))
        else:
            pending.append(up)

    all_elements = fpl_bootstrap["elements"]
    scope_global = False

    def candidates(up: dict[str, Any]) -> list[dict[str, Any]]:
        if scope_global:
            # Understat's club can lag a transfer; names must then match near-exactly
            return [e for e in all_elements if e["id"] not in taken]
        return [e for e in by_team.get(team_key(up["team_title"]), []) if e["id"] not in taken]

    def full(el: dict[str, Any]) -> str:
        return normalise(f"{el['first_name']} {el['second_name']}")

    def attempt(up: dict[str, Any], stage: str) -> Match | None:
        target = normalise(up["player_name"])
        ttoks = _tokens(up["player_name"])
        cands = candidates(up)
        if stage == "exact":
            hit = [e for e in cands if full(e) == target]
            score = 1.0
        elif stage == "web_name":
            hit = [e for e in cands if normalise(e["web_name"]) == target]
            score = 0.95
        elif stage == "tokens" and scope_global:
            hit = [e for e in cands if _tokens(full(e)) == ttoks]
            score = 0.85
        elif stage == "tokens":
            # all of FPL's web_name tokens appear in the Understat name, or vice versa
            hit = [
                e
                for e in cands
                if (w := _tokens(e["web_name"])) and (w <= ttoks or ttoks <= _tokens(full(e)))
            ]
            score = 0.9
        else:
            scored = [
                (max(SequenceMatcher(None, target, full(e)).ratio(),
                     SequenceMatcher(None, target, normalise(e["web_name"])).ratio()), e)
                for e in cands
            ]  # fmt: skip
            floor = 0.93 if scope_global else FUZZY_THRESHOLD
            scored = [(s, e) for s, e in scored if s >= floor]
            scored.sort(key=lambda x: -x[0])
            if len(scored) >= 1 and (len(scored) == 1 or scored[0][0] - scored[1][0] > 0.05):
                s, e = scored[0]
                return Match(
                    int(up["id"]), e["id"], "fuzzy_global" if scope_global else "fuzzy", round(s, 3)
                )
            return None
        if len(hit) == 1:
            return Match(int(up["id"]), hit[0]["id"], stage, score)
        return None

    # run stage by stage across all players so confident matches claim ids first;
    # the second pass drops the club restriction for players Understat lists at a stale club
    for scope_global in (False, True):
        for stage in ("exact", "web_name", "tokens", "fuzzy"):
            still = []
            for up in pending:
                m = attempt(up, stage)
                if m:
                    if scope_global:
                        m = Match(m.understat_id, m.fpl_id, f"global_{m.method}", m.score)
                    taken.add(m.fpl_id)  # type: ignore[arg-type]
                    results.append(m)
                else:
                    still.append(up)
            pending = still
    results += [Match(int(up["id"]), None, "unmatched", 0.0) for up in pending]

    df = pd.DataFrame([m.__dict__ for m in results]).sort_values("understat_id")
    df["fpl_id"] = df["fpl_id"].astype("Int64")
    return df.reset_index(drop=True)


def coverage(
    id_map: pd.DataFrame, fpl_bootstrap: dict[str, Any], min_minutes: int = 90
) -> dict[str, Any]:
    """Share of FPL players with real minutes that have an Understat id."""
    mapped = set(id_map["fpl_id"].dropna().astype(int))
    played = [e for e in fpl_bootstrap["elements"] if e["minutes"] >= min_minutes]
    missing = [e for e in played if e["id"] not in mapped]
    return {
        "fpl_players_with_minutes": len(played),
        "mapped": len(played) - len(missing),
        "coverage": round(1 - len(missing) / len(played), 4) if played else None,
        "missing": sorted(f"{e['web_name']} ({e['minutes']}')" for e in missing),
        "methods": id_map["method"].value_counts().to_dict(),
    }
