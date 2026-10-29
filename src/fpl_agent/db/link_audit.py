"""How much of a season's FPL playing time has detailed (FotMob) match data joined to it.

The model's per-player rates (`model.players`) read FotMob rows by canonical player id, so an FPL
player whose FotMob record was never linked gets position priors instead of their own xG, xA and
defensive actions. This report measures that gap per season, weighted by FPL minutes, and lists
the unlinked players with the best reason the stored data gives:

* the reason ingestion recorded for a FotMob player of the same normalised name (ingest_state,
  source 'link': single-word name, position differs, namesakes, no name match);
* `no FotMob player with this name`: no FotMob id carries that normalised name;
* `unrecorded`: a FotMob namesake exists but no reason was kept (files older than the reason log).

A player counts as linked when their canonical id holds a FotMob id (`via: name`) or, where the
`player_links` table exists, an active evidence link valid by `at` points a FotMob-only player to
them (`via: evidence`), unless the evidence stored by then no longer supports that link
(`unsupported_links`, from `link_candidates.revalidate`; projections leave those unlinked too).
Read-only: older files without the newer tables are reported, not changed.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from fpl_agent.data.id_map import normalise
from fpl_agent.db.database import FootballDB
from fpl_agent.db.link_candidates import revalidate

FOTMOB = "fotmob"


def _links_by_target(db: FootballDB, season: int, at: datetime | None) -> dict[int, int]:
    """FPL canonical id -> FotMob-only canonical id, for links active by `at`: written by then
    and supported by evidence by then (no `at`: every active link), made from `season`'s
    evidence: a link from another season's evidence says nothing about this season's data."""
    if "player_links" in getattr(db, "missing_tables", []):
        return {}
    try:
        rows = db.con.execute(
            "SELECT to_player_id, from_player_id FROM player_links WHERE rolled_back_at IS NULL "
            "AND season = ? AND (? IS NULL OR (valid_from <= ? AND recorded_at <= ?))",
            [season, at, at, at]
        ).fetchall()  # fmt: skip
    except Exception:  # noqa: BLE001 - a file without the table has no links
        return {}
    return {int(t): int(f) for t, f in rows}


def coverage(db: FootballDB, season: int, at: datetime | None = None,
             limit: int | None = None) -> dict[str, Any]:  # fmt: skip
    """Linked share of the season's FPL players who played and of their minutes, and the
    unlinked players (most minutes first; `limit` caps the list, never the counts)."""
    played = db.con.execute(
        "SELECT p.player_id, any_value(pl.name), sum(p.minutes) FROM fpl_points_gw p "
        "LEFT JOIN players pl USING (player_id) WHERE p.season = ? AND p.minutes > 0 "
        "AND p.player_id IS NOT NULL GROUP BY p.player_id", [season]
    ).fetchall()  # fmt: skip
    direct = {int(r[0]) for r in db.con.execute(
        "SELECT DISTINCT player_id FROM player_ids WHERE source = ?", [FOTMOB]
    ).fetchall()}  # fmt: skip
    links = _links_by_target(db, season, at)
    unsupported = revalidate(db, season, at) if links else []
    for r in unsupported:  # what the model would not use is not counted as linked
        links.pop(r["to_player_id"], None)
    fm_names = {normalise(r[0]) for r in db.con.execute(
        "SELECT source_name FROM player_ids WHERE source = ? AND source_name IS NOT NULL",
        [FOTMOB]).fetchall()}  # fmt: skip
    reasons = dict(db.con.execute(
        "SELECT key, detail FROM ingest_state WHERE source = 'link' AND key LIKE 'fotmob:%'"
    ).fetchall())  # fmt: skip
    total = linked_min = by_name = by_link = 0
    unlinked = []
    for pid, name, minutes in played:
        minutes = int(minutes or 0)
        total += minutes
        if pid in direct:
            by_name += 1
            linked_min += minutes
        elif pid in links:
            by_link += 1
            linked_min += minutes
        else:
            key = normalise(name or "")
            why = reasons.get(f"fotmob:{key}")
            if why is None:
                why = "unrecorded" if key in fm_names else "no FotMob player with this name"
            unlinked.append({"player_id": pid, "name": name, "minutes": minutes, "reason": why})
    unlinked.sort(key=lambda r: (-r["minutes"], r["player_id"]))
    n = len(played)
    summary: dict[str, int] = {}
    for r in unlinked:
        kind = "position differs" if r["reason"].startswith("position") else r["reason"]
        summary[kind] = summary.get(kind, 0) + 1
    return {
        "season": season, "at": at.isoformat() if at else None,
        "players_played": n, "players_linked": by_name + by_link,
        "linked_by_name": by_name, "linked_by_evidence": by_link,
        "player_share": round((by_name + by_link) / n, 4) if n else None,
        "minutes": total, "minutes_linked": linked_min,
        "minute_share": round(linked_min / total, 4) if total else None,
        "unlinked_reasons": dict(sorted(summary.items(), key=lambda kv: -kv[1])),
        "unlinked": unlinked[:limit] if limit is not None else unlinked,
        "unsupported_links": unsupported,
    }  # fmt: skip
