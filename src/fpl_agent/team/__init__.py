"""The manager's current team: public data, screenshots and corrections merged with evidence.

* `observation`: the validated input schema for a screen read or a correction, and player
  resolution;
* `public`: what FPL's public endpoints say (last deadline + this week's transfers);
* `merge`: one `CurrentTeam` with a status, source and age per field, and the planner state;
* `store`: local, git-ignored storage of observations;
* `report`: report lines.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from fpl_agent.rules import Rules
from fpl_agent.team import store
from fpl_agent.team.merge import CurrentTeam, merge
from fpl_agent.team.observation import Observation, resolve
from fpl_agent.team.public import PublicView, public_view


def current_team(
    *,
    entry: int,
    season: int,
    gameweek: int,
    now: datetime,
    rules: Rules,
    bootstrap: dict,
    public: PublicView | None,
    root: Path | None,
    fixtures=None,
    extra: list[Observation] | None = None,
) -> CurrentTeam:
    """Merge the public view with every stored (and `extra`) observation for the entry.
    `fixtures` is a list or a callable returning one (only read when observations exist)."""
    obs, bad = store.load(root, entry) if root else ([], [])
    obs = obs + list(extra or [])
    fx = (fixtures() if callable(fixtures) else fixtures) if obs else []
    prices = {e["id"]: int(e["now_cost"]) for e in bootstrap["elements"]}
    names = {e["id"]: e.get("web_name") or str(e["id"]) for e in bootstrap["elements"]}
    team = merge(
        public,
        [resolve(o, bootstrap, fx or []) for o in obs],
        entry=entry,
        season=season,
        gameweek=gameweek,
        now=now,
        rules=rules,
        prices=prices,
        names=names,
    )
    team.notes += bad
    return team


__all__ = ["CurrentTeam", "Observation", "PublicView", "current_team", "public_view"]
