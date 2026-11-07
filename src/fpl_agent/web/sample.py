"""The labelled sample team: a fictional squad for demonstrations that need no live lookup.

It is built from the bootstrap of the published data revision: a legal 15 (2/5/5/3, at most
three per club, within £100.0m) of available players chosen by FPL season points per £m. It has
no season history, so no rank, points or chip history is shown for it; the free-transfer count
is whatever the engine reconstructs for a team with no recorded transfers (labelled estimated).
The website always shows it under a "Sample data" banner and never substitutes it for a failed
lookup of a real team.
"""

from __future__ import annotations

import json
from pathlib import Path

from fpl_agent.data.players import POSITIONS

SAMPLE_ENTRY = {
    "id": 0,
    "name": "Sample XI",
    "player_first_name": "Sample",
    "player_last_name": "Manager",
    "player_region_name": None,
    "started_event": None,
    "years_active": None,
    "summary_overall_points": None,
    "summary_overall_rank": None,
    "summary_event_points": None,
    "summary_event_rank": None,
    "leagues": {"classic": []},
}
COUNTS = {"GKP": 2, "DEF": 5, "MID": 5, "FWD": 3}
BUDGET = 1000


def pick_squad(bootstrap: dict) -> list[dict]:
    els = [
        e
        for e in bootstrap["elements"]
        if e.get("status") == "a" and (e.get("chance_of_playing_next_round") in (None, 100))
    ]
    value = lambda e: (float(e.get("total_points") or 0) + 1) / max(e["now_cost"], 1)  # noqa: E731
    chosen: list[dict] = []
    clubs: dict[int, int] = {}
    # cheapest legal fill first per position, then upgrade greedily by points within budget
    for pos, n in COUNTS.items():
        pool = sorted(
            (e for e in els if POSITIONS[e["element_type"]] == pos),
            key=lambda e: (-value(e), e["id"]),
        )
        got = 0
        for e in pool:
            if got == n:
                break
            if clubs.get(e["team"], 0) >= 3:
                continue
            chosen.append(e)
            clubs[e["team"]] = clubs.get(e["team"], 0) + 1
            got += 1
    spent = sum(e["now_cost"] for e in chosen)
    for i, e in sorted(enumerate(chosen), key=lambda x: x[1]["total_points"]):
        pos = POSITIONS[e["element_type"]]
        better = sorted(
            (
                x
                for x in els
                if POSITIONS[x["element_type"]] == pos
                and x not in chosen
                and x["total_points"] > e["total_points"]
                and (clubs.get(x["team"], 0) < 3 or x["team"] == e["team"])
                and spent - e["now_cost"] + x["now_cost"] <= BUDGET
            ),
            key=lambda x: (-x["total_points"], x["id"]),
        )
        if better:
            x = better[0]
            clubs[e["team"]] -= 1
            clubs[x["team"]] = clubs.get(x["team"], 0) + 1
            spent += x["now_cost"] - e["now_cost"]
            chosen[i] = x
    return chosen


def build(bootstrap: dict, fixtures: list, out: Path, captured_at: float) -> Path:
    ev = next((e for e in bootstrap["events"] if e.get("is_next")), None)
    if ev is None:
        raise ValueError("no upcoming gameweek in the bootstrap")
    squad = pick_squad(bootstrap)
    order = {"GKP": 0, "DEF": 1, "MID": 2, "FWD": 3}
    gk = [e for e in squad if POSITIONS[e["element_type"]] == "GKP"]
    out_ = [e for e in squad if POSITIONS[e["element_type"]] != "GKP"]
    out_.sort(key=lambda e: -float(e.get("total_points") or 0))
    xi = [gk[0]] + sorted(out_[:10], key=lambda e: order[POSITIONS[e["element_type"]]])
    bench = [gk[1]] + out_[10:]
    cap = max(xi[1:], key=lambda e: float(e.get("total_points") or 0))
    vice = max((e for e in xi[1:] if e is not cap), key=lambda e: float(e.get("total_points") or 0))
    picks = [
        {
            "element": e["id"],
            "position": i + 1,
            "multiplier": 2 if e is cap else (1 if i < 11 else 0),
            "is_captain": e is cap,
            "is_vice_captain": e is vice,
        }
        for i, e in enumerate(xi + bench)
    ]
    spent = sum(e["now_cost"] for e in squad)
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    files = {
        "bootstrap": bootstrap,
        "fixtures": fixtures,
        "entry": {**SAMPLE_ENTRY, "current_event": ev["id"] - 1},
        "history": {"current": [], "past": [], "chips": []},
        "picks": {
            "picks": picks,
            "entry_history": {"event": ev["id"] - 1, "bank": BUDGET - spent, "value": spent},
            "active_chip": None,
        },
        "transfers": [],
    }
    for name, body in files.items():
        (out / f"{name}.json").write_text(json.dumps(body))
    (out / "captured_at.txt").write_text(str(captured_at))
    return out


__all__ = ["build", "pick_squad", "SAMPLE_ENTRY"]
