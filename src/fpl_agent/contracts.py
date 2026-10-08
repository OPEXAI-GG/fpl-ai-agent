"""Shape checks for every external response. Each returns a list of problems (empty = fine), so a
changed format is detected before bad data reaches the database. The same functions run in unit
tests against recorded real responses and in the daily live canary."""

from __future__ import annotations

import io
from typing import Any

from fpl_agent.errors import SourceFormatError


def _keys(obj: Any, required: set[str], where: str) -> list[str]:
    if not isinstance(obj, dict):
        return [f"{where}: expected object, got {type(obj).__name__}"]
    missing = required - obj.keys()
    return [f"{where}: missing {sorted(missing)}"] if missing else []


def fpl_bootstrap(raw: Any, full_season: bool = False) -> list[str]:
    errs = _keys(raw, {"events", "teams", "elements", "element_types"}, "bootstrap")
    if errs:
        return errs
    if not raw["events"] or not raw["teams"] or not raw["elements"]:
        return ["bootstrap: events/teams/elements empty"]
    errs += _keys(raw["events"][0], {"id", "deadline_time", "finished", "is_next"}, "event")
    errs += _keys(raw["teams"][0], {"id", "name", "short_name"}, "team")
    errs += _keys(
        raw["elements"][0],
        {"id", "first_name", "second_name", "web_name", "team", "element_type", "now_cost",
         "status", "minutes", "total_points", "selected_by_percent"},
        "element",
    )  # fmt: skip
    if full_season:
        if len(raw["events"]) != 38:
            errs.append(f"bootstrap: expected 38 events, got {len(raw['events'])}")
        if len(raw["teams"]) != 20:
            errs.append(f"bootstrap: expected 20 teams, got {len(raw['teams'])}")
        if len(raw["elements"]) < 400:
            errs.append(f"bootstrap: only {len(raw['elements'])} players")
    return errs


def fpl_fixtures(raw: Any) -> list[str]:
    if not isinstance(raw, list) or not raw:
        return ["fixtures: expected a non-empty list"]
    return _keys(raw[0], {"id", "event", "team_h", "team_a", "finished", "kickoff_time"}, "fixture")


def fpl_live(raw: Any) -> list[str]:
    errs = _keys(raw, {"elements"}, "live")
    if errs:
        return errs
    if not raw["elements"]:
        return []  # a GW that hasn't started is legitimately empty
    errs += _keys(raw["elements"][0], {"id", "stats", "explain"}, "live element")
    if not errs:
        errs += _keys(raw["elements"][0]["stats"], {"minutes", "total_points", "bps"}, "live stats")
    return errs


def understat_league(raw: Any) -> list[str]:
    errs = _keys(raw, {"teams", "players", "dates"}, "understat league")
    if errs:
        return errs
    if not raw["dates"]:
        return ["understat league: no fixtures"]
    errs += _keys(raw["dates"][0], {"id", "isResult", "h", "a", "goals", "xG", "datetime"}, "date")
    if raw["players"]:
        errs += _keys(raw["players"][0], {"id", "player_name", "team_title", "time"}, "player")
    return errs


def understat_player(raw: Any) -> list[str]:
    errs = _keys(raw, {"player", "matches", "shots"}, "understat player")
    if errs:
        return errs
    errs += _keys(raw["player"], {"id", "name"}, "player")
    if raw["matches"]:
        errs += _keys(
            raw["matches"][0], {"id", "time", "xG", "h_team", "a_team", "season"}, "match"
        )
    if raw["shots"]:
        errs += _keys(raw["shots"][0], {"id", "xG", "situation", "match_id", "X", "Y"}, "shot")
    return errs


def football_data_csv(text: str, div: str = "") -> list[str]:
    import pandas as pd

    if "<html" in text[:500].lower():
        return [f"football-data {div}: got HTML, not CSV"]
    try:
        df = pd.read_csv(io.StringIO(text.lstrip("﻿")), nrows=5)
    except Exception as exc:  # noqa: BLE001
        return [f"football-data {div}: unreadable CSV ({exc})"]
    need = {"Date", "HomeTeam", "AwayTeam", "FTHG", "FTAG"}
    missing = need - set(df.columns)
    return [f"football-data {div}: missing columns {sorted(missing)}"] if missing else []


def require(problems: list[str], source: str) -> None:
    """Raise SourceFormatError if a contract check found problems."""
    if problems:
        raise SourceFormatError(f"{source}: " + "; ".join(problems))
