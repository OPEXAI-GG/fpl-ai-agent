"""Pure functions for FPL scoring, bonus, auto-subs and prices. All numbers come from the rules
YAML (`rules/<season>.yaml`); nothing is hard-coded here."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

RULES_DIR = Path(__file__).resolve().parents[3] / "rules"


@dataclass(frozen=True)
class Rules:
    data: dict[str, Any]

    @classmethod
    def load(cls, season: int | str = 2026, directory: Path | None = None) -> Rules:
        d = directory or RULES_DIR
        matches = sorted(d.glob(f"{season}_*.yaml"))
        if not matches:
            raise FileNotFoundError(f"no rules file for season {season} in {d}")
        return cls(yaml.safe_load(matches[0].read_text()))

    @classmethod
    def nearest(cls, season: int, directory: Path | None = None) -> Rules:
        """The rules file for `season`, or the closest one we have (for back-testing past seasons
        under the current scoring; the difference is reported by the caller, not hidden)."""
        d = directory or RULES_DIR
        seasons = sorted(int(p.name.split("_")[0]) for p in d.glob("*_*.yaml"))
        if not seasons:
            raise FileNotFoundError(f"no rules files in {d}")
        best = min(seasons, key=lambda s: (abs(s - season), s))
        return cls.load(best, d)

    def __getitem__(self, key: str) -> Any:
        return self.data[key]


def score(stats: dict[str, Any], position: str, rules: Rules) -> dict[str, int]:
    """Points per scoring item for one player in one match. `stats` uses FPL's field names
    (minutes, goals_scored, assists, clean_sheets, goals_conceded, own_goals, penalties_saved,
    penalties_missed, yellow_cards, red_cards, saves, bonus, defensive_contribution)."""
    sc = rules["scoring"]
    g = lambda k: int(float(stats.get(k) or 0))  # noqa: E731
    minutes = g("minutes")
    out: dict[str, int] = {}
    if minutes <= 0:
        return {"total": 0}
    out["minutes"] = sc["minutes_long"] if minutes >= 60 else sc["minutes_short"]
    out["goals_scored"] = g("goals_scored") * sc["goal"][position]
    out["assists"] = g("assists") * sc["assist"]
    if minutes >= 60 and g("clean_sheets"):
        out["clean_sheets"] = sc["clean_sheet"][position]
    out["goals_conceded"] = (g("goals_conceded") // sc["goals_conceded_per"]) * sc[
        "goals_conceded"
    ][position]
    if position == "GKP":
        out["saves"] = (g("saves") // sc["saves_per"]) * sc["save_points"]
    out["penalties_saved"] = g("penalties_saved") * sc["penalty_saved"]
    out["penalties_missed"] = g("penalties_missed") * sc["penalty_missed"]
    out["yellow_cards"] = g("yellow_cards") * sc["yellow_card"]
    out["red_cards"] = g("red_cards") * sc["red_card"]
    out["own_goals"] = g("own_goals") * sc["own_goal"]
    out["bonus"] = g("bonus") * sc["bonus"]
    dc = sc["defcon"]
    if position in dc["threshold"] and g("defensive_contribution") >= dc["threshold"][position]:
        out["defensive_contribution"] = dc["points"] * dc["cap_per_match"]
    out = {k: v for k, v in out.items() if v != 0 or k == "minutes"}
    out["total"] = sum(out.values())
    return out


def bonus(match_bps: dict[Any, int], rules: Rules | None = None) -> dict[Any, int]:
    """Bonus points from one match's BPS. Ties: equal first places all get 3 (the next place is
    skipped); a tie for second gives each 2 and nobody gets 1; a tie for third gives each 1."""
    awards = rules["bonus_points"] if rules else [3, 2, 1]
    ordered = sorted(match_bps.items(), key=lambda kv: -kv[1])
    out: dict[Any, int] = {}
    place = 0
    i = 0
    while i < len(ordered) and place < len(awards):
        j = i
        while j < len(ordered) and ordered[j][1] == ordered[i][1]:
            j += 1
        for pid, _ in ordered[i:j]:
            out[pid] = awards[place]
        place += j - i
        i = j
    return out


def _valid_formation(positions: list[str], rules: Rules) -> bool:
    lo, hi = rules["squad"]["formation_min"], rules["squad"]["formation_max"]
    return len(positions) == rules["squad"]["starting"] and all(
        lo[p] <= positions.count(p) <= hi[p] for p in lo
    )


def auto_sub(
    xi: list[Any],
    bench: list[Any],
    minutes: dict[Any, int],
    position: dict[Any, str],
    rules: Rules,
) -> list[Any]:
    """Final XI after FPL's automatic substitutions. Bench is in priority order; a bench player
    who played 0 minutes can't come on; the GK only swaps with the bench GK; formation limits
    must hold after each swap. Players with 0 minutes are replaced in squad-slot order."""
    final = list(xi)
    used: set[Any] = set()
    for starter in list(xi):
        if minutes.get(starter, 0) > 0:
            continue
        for sub in bench:
            if sub in used or minutes.get(sub, 0) <= 0:
                continue
            if (position[starter] == "GKP") != (position[sub] == "GKP"):
                continue
            trial = [sub if p == starter else p for p in final]
            if _valid_formation([position[p] for p in trial], rules):
                final = trial
                used.add(sub)
                break
    return final


def effective_captain(captain: Any, vice: Any, minutes: dict[Any, int]) -> Any | None:
    """The vice-captain takes the armband only if the captain played 0 minutes."""
    if minutes.get(captain, 0) > 0:
        return captain
    return vice if minutes.get(vice, 0) > 0 else None


def selling_price(buy: int, now: int, rules: Rules | None = None) -> int:
    """Selling price in tenths of £m: you keep half of any rise, rounded down; falls are in full."""
    if now <= buy:
        return now
    return buy + (now - buy) // 2
