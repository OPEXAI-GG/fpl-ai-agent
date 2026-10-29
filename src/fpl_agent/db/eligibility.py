"""Which past gameweeks a retrospective evaluation may treat as genuine decision-time evidence.

A gameweek is `eligible` only when what a decision used was actually observed before its
deadline: the official deadline was itself recorded before it passed, and an FPL bootstrap
snapshot of that gameweek (observed, not rebuilt from the vaastav archive) was taken by then
holding at least COVERAGE of the very players who later scored (the same FPL ids, from one
pull: the latest observed one by the cutoff). Otherwise it is `reconstructed` (some inputs exist but
were rebuilt or stamped after the fact, or the deadline is approximated from kickoff) or
`unavailable` (no snapshot of the gameweek at all). Applying a cutoff to reconstructed or missing
inputs does not make them observed: replays over those gameweeks are exploratory, never
evidence of decision performance. Matches with unresolved FPL/FotMob conflicts (F-16) are
listed for exclusion. Read-only.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

import pandas as pd

from fpl_agent.db.database import FootballDB
from fpl_agent.db.quality import FPL_FIXTURE_MATCHES, source_conflicts
from fpl_agent.model.features import OBSERVED_SNAPSHOT

COVERAGE = 0.95  # share of the gameweek's scoring players the observed snapshot must hold
DEADLINE_LEAD = timedelta(minutes=90)  # as project.approx_deadline


def _has(db: FootballDB, table: str) -> bool:
    return table not in getattr(db, "missing_tables", [])


def gameweek(db: FootballDB, season: int, gw: int) -> dict[str, Any]:
    """The evidence available for one gameweek's decision, and its class."""
    first_ko = db.con.execute(
        "SELECT min(kickoff) FROM fpl_points_gw WHERE season = ? AND gw = ? AND fixture_id <> 0",
        [season, gw]).fetchone()[0]  # fmt: skip
    official = None
    if _has(db, "fpl_deadlines"):
        official = db.con.execute(
            "SELECT deadline_time FROM fpl_deadlines WHERE season = ? AND gw = ? AND "
            "observed_at <= deadline_time ORDER BY observed_at DESC LIMIT 1", [season, gw]
        ).fetchone()  # fmt: skip
    if official:
        cutoff, cutoff_kind = official[0], "official"
    elif first_ko is not None:
        cutoff, cutoff_kind = first_ko - DEADLINE_LEAD, "approximate"
    else:
        cutoff, cutoff_kind = None, "unknown"
    # one coherent snapshot: the latest observed pull of this gameweek taken by the cutoff (a
    # bootstrap pull stamps every player with the same snapshot_at), never a pool of several
    pull = db.con.execute(
        f"SELECT max(snapshot_at) FROM fpl_players_gw WHERE season = ? AND gw = ? AND "
        f"coalesce({OBSERVED_SNAPSHOT}, false) AND snapshot_at <= ?", [season, gw, cutoff]
    ).fetchone()[0]  # fmt: skip
    required, observed, rebuilt = db.con.execute(
        "WITH need AS (SELECT DISTINCT fpl_id FROM fpl_points_gw WHERE season = ? AND gw = ? "
        "AND fixture_id <> 0), "
        "seen AS (SELECT DISTINCT fpl_id FROM fpl_players_gw WHERE season = ? AND gw = ? "
        "AND snapshot_at = ?), "
        "rebuilt AS (SELECT DISTINCT fpl_id FROM fpl_players_gw WHERE season = ? AND gw = ? "
        f"AND NOT coalesce({OBSERVED_SNAPSHOT}, false)) "
        "SELECT count(*), count(*) FILTER (WHERE fpl_id IN (SELECT fpl_id FROM seen)), "
        "count(*) FILTER (WHERE fpl_id IN (SELECT fpl_id FROM rebuilt)) FROM need",
        [season, gw, season, gw, pull, season, gw]).fetchone()  # fmt: skip
    missing = required - observed
    odds = 0
    if cutoff is not None and _has(db, "match_odds_log"):
        odds = db.con.execute(
            "SELECT count(DISTINCT m.match_id) FROM matches m JOIN match_odds_log o USING "
            "(match_id) WHERE m.competition_id = 'ENG1' AND m.season = ? AND m.matchday = ? "
            "AND NOT o.closing AND o.ingested_at <= ?", [season, gw, cutoff]
        ).fetchone()[0]  # fmt: skip
    coverage = observed / required if required else 0.0
    reasons = []
    if cutoff_kind != "official":
        reasons.append(f"deadline {cutoff_kind}: no official deadline recorded before it passed")
    if not required:
        reasons.append("no scoring players recorded for this gameweek")
    elif coverage < COVERAGE:
        reasons.append(f"observed pre-deadline snapshot covers {observed} of the {required} "
                       f"scoring players ({missing} missing)")  # fmt: skip
    if rebuilt:
        reasons.append(f"{rebuilt} scoring players' snapshots rebuilt from the archive "
                       "(kickoff-stamped)")  # fmt: skip
    if not reasons:
        cls = "eligible"
    elif observed or rebuilt:
        cls = "reconstructed"
    else:
        cls = "unavailable"
    return {"season": season, "gw": gw, "class": cls, "reasons": reasons,
            "cutoff": cutoff.isoformat() if cutoff else None, "cutoff_kind": cutoff_kind,
            "snapshot_used": pull.isoformat() if pull else None,
            "required_players": required, "covered_players": observed, "missing_players": missing,
            "coverage": round(coverage, 4), "reconstructed_snapshot_players": rebuilt,
            "pre_cutoff_odds_matches": odds}  # fmt: skip


def manifest(db: FootballDB, season: int) -> dict[str, Any]:
    """Every gameweek of `season` with official points, classified, plus the matches excluded
    from validated evaluations. `eligible_gws` is the only set that supports a claim about
    decision performance."""
    gws = [r[0] for r in db.con.execute(
        "SELECT DISTINCT gw FROM fpl_points_gw WHERE season = ? ORDER BY 1", [season]
    ).fetchall()]  # fmt: skip
    rows = [gameweek(db, season, g) for g in gws]
    conflicts = source_conflicts(db, season)
    by_class: dict[str, list[int]] = {"eligible": [], "reconstructed": [], "unavailable": []}
    for r in rows:
        by_class[r["class"]].append(r["gw"])
    return {
        "season": season,
        "eligible_gws": by_class["eligible"],
        "reconstructed_gws": by_class["reconstructed"],
        "unavailable_gws": by_class["unavailable"],
        "excluded_matches": sorted({c["match_id"] for c in conflicts}),
        "conflicts": conflicts,
        "missing_tables": list(getattr(db, "missing_tables", [])),
        "gameweeks": rows,
        "rule": "only eligible gameweeks, without excluded matches, support decision-performance "
        "claims; reconstructed/unavailable ones are exploratory",
    }


class NotEligible(Exception):
    """A validated evaluation was asked for gameweeks that are not eligible evidence."""


def excluded_fixtures(db: FootballDB) -> tuple[set[int], set[tuple[int, int]]]:
    """(canonical match ids, (season, FPL fixture id)) of every match with an unresolved
    FPL/FotMob conflict, in any season (history spans seasons). A fixture id is excluded only
    in the season of the conflicting match: the same id in another season is another match."""
    mids = {c["match_id"] for c in source_conflicts(db)}
    if not mids:
        return set(), set()
    marks = ", ".join("?" for _ in mids)
    rows = db.con.execute(
        f"SELECT DISTINCT x.season, x.fixture_id FROM fpl_points_gw x JOIN {FPL_FIXTURE_MATCHES} "
        "i ON i.season = x.season AND i.source_id = x.fixture_id::VARCHAR "
        f"WHERE i.match_id IN ({marks})", sorted(mids)).fetchall()  # fmt: skip
    return mids, {(int(s), int(f)) for s, f in rows}


def validated_inputs(
    db: FootballDB, season: int, gws: list[int], frame: pd.DataFrame, pm: pd.DataFrame
) -> tuple[pd.DataFrame, pd.DataFrame, dict[int, datetime], set[tuple[int, int]]]:
    """The inputs of a validated evaluation: refuses (NotEligible) unless every gameweek is
    eligible; removes every row of an excluded match from the official-points frame and the
    per-match frame (all players of the match, all seasons); returns each gameweek's official
    cutoff and the excluded (season, fixture) pairs, so scoring can skip player-gameweeks
    that include one. Team results (strength ratings) are kept: the conflicts are about which
    player scored or how long he played, not the score."""
    m = manifest(db, season)
    bad = [g for g in gws if g not in m["eligible_gws"]]
    if bad or not gws:
        raise NotEligible(f"season {season} gameweeks {bad or gws} are not eligible evidence "
                          f"(eligible: {m['eligible_gws'] or 'none'}); run without validation "
                          "and label the result exploratory")  # fmt: skip
    mids, fixtures = excluded_fixtures(db)
    if fixtures and not frame.empty:
        key = list(zip(frame["season"].astype(int), frame["fixture_id"].astype(int), strict=True))
        frame = frame[[k not in fixtures for k in key]]
    if mids and not pm.empty:
        pm = pm[~pm["match_id"].isin(mids)]
    cut = {r["gw"]: datetime.fromisoformat(r["cutoff"]) for r in m["gameweeks"] if r["gw"] in gws}
    return frame, pm, cut, fixtures
