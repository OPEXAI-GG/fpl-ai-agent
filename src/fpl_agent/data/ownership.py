"""What the managers you compete with own: a sample of the top of FPL's overall league.

For overall rank, the players that matter are those owned and captained by the managers near
the top, not the whole game's `selected_by_percent`. Each gameweek a fixed-seed random sample of
managers from the top `TOP` of the overall standings is read through the public picks endpoint
(their squads at the last deadline, the latest that is public), giving per player:

* `owned`: percent of the sample with him in the squad;
* `eo`: effective ownership, percent counting his multiplier (starting 1, captain 2, triple
  captain 3, bench 0), i.e. how many points the average top manager gets per point he scores.

Stored once per gameweek in `field_ownership`; later runs read it back.
"""

from __future__ import annotations

import logging
import random

import pandas as pd

from fpl_agent.db.database import FootballDB, now

log = logging.getLogger(__name__)

OVERALL_LEAGUE = 314
TOP = 10_000
PAGE_SIZE = 50
SAMPLE = 200
PAGES = 6  # standings pages read to draw the sample from


def sample_field(fpl, gw: int, sample: int = SAMPLE, top: int = TOP,
                 pages: int = PAGES, seed: int | None = None) -> pd.DataFrame:  # fmt: skip
    """Ownership among `sample` managers drawn from the top `top`, from their picks for `gw`
    (a finished or locked gameweek). Columns: fpl_id, owned, eo, sample."""
    rng = random.Random(seed if seed is not None else gw)
    last_page = max(1, top // PAGE_SIZE)
    entries: list[int] = []
    for page in sorted(rng.sample(range(1, last_page + 1), min(pages, last_page))):
        rows = fpl.league_standings_raw(OVERALL_LEAGUE, page).get("standings", {})
        entries += [r["entry"] for r in rows.get("results", [])]
    if not entries:
        return pd.DataFrame(columns=["fpl_id", "owned", "eo", "sample"])
    chosen = rng.sample(entries, min(sample, len(entries)))
    owned: dict[int, int] = {}
    eo: dict[int, int] = {}
    n = 0
    for entry in chosen:
        try:
            picks = fpl.entry_picks_raw(entry, gw).get("picks", [])
        except Exception as exc:  # noqa: BLE001  (one unreadable manager is skipped)
            log.warning("picks for %s unreadable: %s", entry, type(exc).__name__)
            continue
        if len(picks) != 15:
            continue
        n += 1
        for p in picks:
            owned[p["element"]] = owned.get(p["element"], 0) + 1
            eo[p["element"]] = eo.get(p["element"], 0) + int(p.get("multiplier", 1))
    if not n:
        return pd.DataFrame(columns=["fpl_id", "owned", "eo", "sample"])
    return pd.DataFrame(
        [
            {"fpl_id": k, "owned": 100.0 * v / n, "eo": 100.0 * eo[k] / n, "sample": n}
            for k, v in owned.items()
        ]  # fmt: skip
    )


def field_ownership(db: FootballDB, fpl, season: int, gw: int) -> pd.DataFrame:
    """The sample for the gameweek being planned (`gw`), read from the last deadline's picks.
    Taken once per gameweek and stored; an empty frame when it cannot be read."""
    cols = ["fpl_id", "owned", "eo", "sample"]
    have = db.query(
        "SELECT fpl_id, owned, eo, sample FROM field_ownership WHERE season = ? AND gw = ?",
        [season, gw],
    )
    if not have.empty or gw <= 1:
        return have if not have.empty else pd.DataFrame(columns=cols)
    try:
        df = sample_field(fpl, gw - 1)
    except Exception as exc:  # noqa: BLE001  (the plan falls back to overall ownership)
        log.warning("field sample failed: %s", exc)
        return pd.DataFrame(columns=cols)
    if not df.empty:
        db.upsert("field_ownership", df.assign(season=season, gw=gw, fetched_at=now()))
    return df
