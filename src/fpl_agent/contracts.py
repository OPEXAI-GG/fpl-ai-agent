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


def _each(items: list[Any], required: set[str], where: str, limit: int = 3) -> list[str]:
    """_keys for every row, not just the first: a malformed row anywhere is caught. Reports the
    first few bad rows by index so the message stays short."""
    errs: list[str] = []
    for i, item in enumerate(items):
        errs += _keys(item, required, f"{where}[{i}]")
        if len(errs) >= limit:
            return errs + ([f"{where}: more rows malformed"] if i < len(items) - 1 else [])
    return errs


def fpl_bootstrap(raw: Any, full_season: bool = False) -> list[str]:
    errs = _keys(raw, {"events", "teams", "elements", "element_types"}, "bootstrap")
    if errs:
        return errs
    if not raw["events"] or not raw["teams"] or not raw["elements"]:
        return ["bootstrap: events/teams/elements empty"]
    if not all(isinstance(raw[k], list) for k in ("events", "teams", "elements")):
        return ["bootstrap: events/teams/elements must be lists"]
    errs += _each(raw["events"], {"id", "deadline_time", "finished", "is_next"}, "event")
    errs += _each(raw["teams"], {"id", "name", "short_name"}, "team")
    errs += _each(
        raw["elements"],
        {"id", "first_name", "second_name", "web_name", "team", "element_type", "now_cost",
         "status", "minutes", "total_points", "selected_by_percent"},
        "element",
    )  # fmt: skip
    if not errs:
        teams = {t["id"] for t in raw["teams"]}
        orphans = [e["id"] for e in raw["elements"] if e["team"] not in teams]
        if orphans:
            errs.append(f"bootstrap: {len(orphans)} elements with unknown team, e.g. {orphans[:3]}")
    if full_season:
        if len(raw["events"]) != 38:
            errs.append(f"bootstrap: expected 38 events, got {len(raw['events'])}")
        if len(raw["teams"]) != 20:
            errs.append(f"bootstrap: expected 20 teams, got {len(raw['teams'])}")
        if len(raw["elements"]) < 400:
            errs.append(f"bootstrap: only {len(raw['elements'])} players")
    return errs


def fpl_fixtures(raw: Any, team_ids: set[int] | None = None) -> list[str]:
    """With `team_ids` (the bootstrap's teams) every fixture must also name known teams."""
    if not isinstance(raw, list) or not raw:
        return ["fixtures: expected a non-empty list"]
    errs = _each(raw, {"id", "event", "team_h", "team_a", "finished", "kickoff_time"}, "fixture")
    if not errs and team_ids is not None:
        bad = [f["id"] for f in raw if f["team_h"] not in team_ids or f["team_a"] not in team_ids]
        if bad:
            errs.append(f"fixtures: {len(bad)} with unknown team, e.g. {bad[:3]}")
    return errs


def fpl_live(raw: Any, finished: bool = False) -> list[str]:
    """A GW that hasn't started is legitimately empty; a `finished` one never is (an empty or
    truncated feed for it must not be stored as "nobody played")."""
    errs = _keys(raw, {"elements"}, "live")
    if errs:
        return errs
    if not isinstance(raw["elements"], list):
        return ["live: elements is not a list"]
    if not raw["elements"]:
        return ["live: finished gameweek has no elements"] if finished else []
    errs += _each(raw["elements"], {"id", "stats", "explain"}, "live element")
    if not errs:
        errs += _each([e["stats"] for e in raw["elements"]],
                      {"minutes", "total_points", "bps"}, "live stats")  # fmt: skip
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


def fotmob_league(raw: Any) -> list[str]:
    errs = _keys(raw, {"details", "fixtures"}, "fotmob league")
    if errs:
        return errs
    errs += _keys(raw["details"], {"id", "name", "selectedSeason"}, "fotmob details")
    errs += _keys(raw["fixtures"], {"allMatches"}, "fotmob fixtures")
    if errs:
        return errs
    matches = raw["fixtures"]["allMatches"]
    if not isinstance(matches, list):
        return ["fotmob fixtures: allMatches is not a list"]
    if matches:
        errs += _keys(matches[0], {"id", "home", "away", "status"}, "fotmob fixture")
        if not errs:
            errs += _keys(matches[0]["home"], {"id", "name"}, "fotmob fixture home")
            errs += _keys(matches[0]["status"], {"utcTime", "finished"}, "fotmob fixture status")
    return errs


def fotmob_match(raw: Any) -> list[str]:
    errs = _keys(raw, {"general", "header", "content"}, "fotmob match")
    if errs:
        return errs
    errs += _keys(raw["general"], {"matchId", "homeTeam", "awayTeam", "finished"}, "fotmob general")
    errs += _keys(raw["content"], {"lineup", "playerStats", "shotmap", "stats"}, "fotmob content")
    if errs:
        return errs
    lineup = raw["content"]["lineup"]
    errs += _keys(lineup, {"homeTeam", "awayTeam"}, "fotmob lineup")
    if not errs and lineup["homeTeam"]:
        errs += _keys(lineup["homeTeam"], {"id", "name", "starters", "subs"}, "fotmob lineup team")
    stats = raw["content"]["playerStats"]
    if stats:
        first = next(iter(stats.values()))
        errs += _keys(first, {"id", "name", "stats"}, "fotmob playerStats entry")
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
