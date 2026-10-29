"""Apply, undo and read evidence links between FotMob-only records and FPL players.

Nothing here runs on its own: `apply` is reached only from `db-links --apply`. It recomputes the
conservative proposals (`link_candidates.history`) at the given cutoff, optionally restricted to a
reviewed proposal file (every reviewed pair must still be accepted, or nothing is applied), and
writes them as one batch in one transaction. Source rows are never changed: readers map ids at
read time (`resolve`), only for links whose `valid_from` is at or before their cutoff. `undo`
marks a batch rolled back (the rows stay, as the record of what was applied and undone), which
returns every reader and report to exactly its previous view of the data; it does not remove the
`player_links` table or other empty tables an apply created on an older file. Applying the same
proposals again adds nothing while their links are active; after an undo it adds a new batch.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime
from typing import Any

import pandas as pd

from fpl_agent.db.database import FootballDB, now
from fpl_agent.db.link_candidates import history, revalidate

SOURCE = "fotmob"


class LinkConflict(Exception):
    """The batch would give a record or a player a second active partner; nothing applied."""


def _active(db: FootballDB) -> pd.DataFrame:
    return db.query("SELECT link_id, batch_id, from_player_id, to_player_id, valid_from "
                    "FROM player_links WHERE rolled_back_at IS NULL")  # fmt: skip


def apply(db: FootballDB, season: int, cutoff: datetime,
          reviewed: list[dict[str, Any]] | None = None) -> dict[str, Any]:  # fmt: skip
    """Link the pairs the rule accepts at `cutoff` (or the `reviewed` subset of them) as one
    batch. Returns the batch id and what was added, already active, or refused."""
    active = _active(db)
    same = set(zip(active["from_player_id"], active["to_player_id"], strict=True))
    # an active link takes its pair (and both its ids) out of the proposals, so a retry finds
    # it here instead, and a new batch cannot reuse an id already linked
    proposals = history(db, season, cutoff)["accepted"]
    kept: list[tuple[int, int]] = []
    if reviewed is not None:
        fresh = {(a["fpl_player_id"], a["candidate"]): a for a in proposals}
        want = [(int(r["fpl_player_id"]), int(r["candidate"])) for r in reviewed]
        kept = [w for w in want if (w[1], w[0]) in same]
        stale = [w for w in want if w not in fresh and (w[1], w[0]) not in same]
        if stale:
            raise LinkConflict(f"reviewed pairs no longer accepted by the rule: {stale}")
        proposals = [fresh[w] for w in want if w in fresh]
    new = proposals
    stamp = now()
    pairs = [(a["candidate"], a["fpl_player_id"]) for a in new]
    digest = hashlib.sha256(json.dumps([season, cutoff.isoformat(), pairs]).encode()).hexdigest()
    # unique even for the same pairs applied again within one second (an undo stays on record)
    batch = f"links-{digest[:12]}-{stamp:%Y%m%dT%H%M%S}-{uuid.uuid4().hex[:8]}"
    rows = [{
        "link_id": f"{batch}:{a['candidate']}", "batch_id": batch, "source": SOURCE,
        "season": season, "from_player_id": int(a["candidate"]),
        "to_player_id": int(a["fpl_player_id"]),
        "valid_from": datetime.fromisoformat(a["valid_from"]),
        "evidence": json.dumps({**a, "cutoff": cutoff.isoformat()}, default=str),
        "recorded_at": stamp, "rolled_back_at": None,
    } for a in new]  # fmt: skip
    if rows:
        with db.transaction():
            frame = pd.DataFrame(rows)
            db.con.register("_links", frame)
            try:  # insert, never replace: an existing id is an error, not an overwrite
                db.con.execute(f"INSERT INTO player_links ({', '.join(frame.columns)}) "
                               f"SELECT {', '.join(frame.columns)} FROM _links")  # fmt: skip
            finally:
                db.con.unregister("_links")
            dup = db.con.execute(
                "SELECT count(*) FROM (SELECT from_player_id FROM player_links WHERE "
                "rolled_back_at IS NULL GROUP BY 1 HAVING count(*) > 1 UNION ALL "
                "SELECT to_player_id FROM player_links WHERE rolled_back_at IS NULL "
                "GROUP BY 1 HAVING count(*) > 1)").fetchone()[0]  # fmt: skip
            if dup:
                raise LinkConflict("applying would leave two active links on one id")
    return {"batch_id": batch if rows else None, "added": len(rows),
            "already_active": len(kept), "cutoff": cutoff.isoformat()}  # fmt: skip


def undo(db: FootballDB, batch_id: str) -> int:
    """Roll back one batch; returns how many links it deactivated (0: unknown or already)."""
    with db.transaction():
        n = db.con.execute(
            "SELECT count(*) FROM player_links WHERE batch_id = ? AND rolled_back_at IS NULL",
            [batch_id]).fetchone()[0]  # fmt: skip
        db.con.execute("UPDATE player_links SET rolled_back_at = ? WHERE batch_id = ? "
                       "AND rolled_back_at IS NULL", [now(), batch_id])  # fmt: skip
    return int(n)


def batches(db: FootballDB) -> list[dict[str, Any]]:
    rows = db.con.execute(
        "SELECT batch_id, season, count(*), min(recorded_at), max(rolled_back_at), "
        "count(*) FILTER (WHERE rolled_back_at IS NULL) FROM player_links GROUP BY 1, 2 "
        "ORDER BY 4").fetchall()  # fmt: skip
    return [{"batch_id": b, "season": s, "links": n, "recorded_at": str(r),
             "rolled_back_at": str(back) if back else None, "active": a}
            for b, s, n, r, back, a in rows]  # fmt: skip


def unsupported(db: FootballDB, season: int, decided_at: datetime) -> frozenset[int]:
    """Records whose active link a decision at `decided_at` must not use because the evidence
    stored by then no longer supports it (`link_candidates.revalidate`). Read-only."""
    if "player_links" in getattr(db, "missing_tables", []):
        return frozenset()
    n = db.con.execute(
        "SELECT count(*) FROM player_links WHERE rolled_back_at IS NULL AND season = ? "
        "AND recorded_at <= ?", [season, decided_at]).fetchone()[0]  # fmt: skip
    if not n:
        return frozenset()
    return frozenset(r["from_player_id"] for r in revalidate(db, season, decided_at))


def resolve(pm: pd.DataFrame, as_of: Any, decided_at: datetime | None = None,
            blocked: set[int] | frozenset[int] = frozenset()) -> pd.DataFrame:  # fmt: skip
    """`pm` (features.match_frame) with each linked record's rows credited to its FPL player,
    only for links a decision at `decided_at` could have used: written by then
    (`recorded_at <= decided_at`, default midnight of `as_of`) and supported by evidence by
    `as_of` (`valid_from <= as_of`). A link missing either time is never used. `blocked`: records
    whose link the evidence by then no longer supports (`revalidate`), left unlinked. A row is
    never duplicated: where the player already has their own row for that match, the linked
    record's row is dropped. A link applies to all of the record's rows, of any season: identity
    does not change, and the times above already keep a past decision from seeing it."""
    if pm.empty or "link_to" not in pm.columns:
        return pm
    decided = pd.Timestamp(as_of).normalize() if decided_at is None else pd.Timestamp(decided_at)
    recorded = pm["link_recorded_at"] if "link_recorded_at" in pm.columns else pd.NaT
    use = (pm["link_to"].notna() & (pm["link_valid_from"] <= pd.Timestamp(as_of))
           & (recorded <= decided) & ~pm["player_id"].isin(blocked))  # fmt: skip
    if not use.any():
        return pm
    out = pm.copy()
    out.loc[use, "player_id"] = out.loc[use, "link_to"].astype(out["player_id"].dtype)
    own = set(zip(out.loc[~use, "match_id"], out.loc[~use, "player_id"], strict=True))
    keys = zip(out["match_id"], out["player_id"], strict=True)
    clash = use & pd.Series([k in own for k in keys], index=out.index)
    return out[~clash]
