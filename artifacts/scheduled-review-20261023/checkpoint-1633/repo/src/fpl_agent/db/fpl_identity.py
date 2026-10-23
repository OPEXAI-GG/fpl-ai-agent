"""Season-scoped identity for the live FPL feed.

FPL reissues element, team and fixture ids every season, so a bare `('fpl', id)` key can name a
different player, club or match next year. New mappings are written under `fpl-<season>`; the
older single `fpl` label is read only while its season is *proven* from dependent rows, and
`migrate` relabels those rows (all or nothing, journalled, reversible). The `fpl` label that
stays in `matches.source` and `ingest_log.source` is provenance, a different namespace.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import duckdb

from fpl_agent.db.database import FootballDB, now
from fpl_agent.db.schema import TABLES

LEGACY = "fpl"
JOURNAL = "migration_log"
MIGRATION = "fpl-season-labels-v1"

# kind -> (id table, canonical column, has a source_name column)
KINDS: dict[str, tuple[str, str, bool]] = {
    "player": ("player_ids", "player_id", True),
    "team": ("team_ids", "team_id", True),
    "match": ("match_ids", "match_id", False),
}
PARENTS = {"player": ("players", "player_id"), "team": ("teams", "team_id"),
           "match": ("matches", "match_id")}  # fmt: skip

_JOURNAL_DDL = f"CREATE TABLE IF NOT EXISTS {JOURNAL} ({TABLES[JOURNAL]})"  # raw connections


class IdentityError(Exception):
    """An FPL id that cannot be resolved to one season without guessing."""


class MigrationRefused(IdentityError):
    """The migration found a state it will not change; nothing was written."""

    def __init__(self, problems: list[str]) -> None:
        super().__init__("; ".join(problems))
        self.problems = problems


def season_source(season: int) -> str:
    return f"{LEGACY}-{season}"


def reader_sources(season: int) -> tuple[str, str]:
    """Labels a reader of `season` accepts: the legacy one (callers also filter on the season of
    the dependent row, which is the proof for matches) and the season's own."""
    return LEGACY, season_source(season)


def _dependents(kind: str) -> str:
    """(season, raw_id, canon) rows that depend on a legacy identity row."""
    table = KINDS[kind][0]
    if kind == "player":
        return (
            "SELECT season, fpl_id::VARCHAR AS raw_id, player_id AS canon FROM fpl_players_gw "
            "UNION SELECT season, fpl_id::VARCHAR, player_id FROM fpl_points_gw"
        )
    if kind == "team":
        return (
            "SELECT season, json_extract_string(raw, '$.team') AS raw_id, team_id AS canon "
            "FROM fpl_players_gw"
        )
    # the canonical match's season, plus any season whose points rows name this raw fixture id
    # (a legacy row cannot serve two seasons: that is the id reuse itself)
    return (
        "SELECT season, match_id::VARCHAR AS raw_id, match_id AS canon FROM matches "
        "UNION SELECT p.season, i2.source_id, i2.match_id FROM fpl_points_gw p JOIN "
        f"{table} i2 ON i2.source = '{LEGACY}' AND i2.source_id = p.fixture_id::VARCHAR "
        "WHERE p.fixture_id <> 0"
    )


def _proof_sql(kind: str) -> str:
    """Per legacy row: the seasons its dependent rows prove. Both the raw FPL id and the
    canonical reference must agree; a season that already has its own `fpl-<season>` row for
    this raw id is excluded (those dependents belong to that mapping, not to the legacy row)."""
    table, canon, _ = KINDS[kind]
    dep = _dependents(kind)
    if kind == "match":
        join = "d.canon = i.canon"
    else:
        join = "d.raw_id = i.source_id AND d.canon = i.canon"
    return (
        f"WITH l AS (SELECT i.source_id, i.{canon} AS canon FROM {table} i "
        f"WHERE i.source = '{LEGACY}' {{extra}}), "
        "p AS (SELECT l.source_id, d.season FROM l JOIN ("
        + dep
        + ") d ON "
        + join.replace("i.", "l.")
        + f" WHERE NOT EXISTS (SELECT 1 FROM {table} n WHERE "
        f"n.source = '{LEGACY}-' || d.season::VARCHAR AND n.source_id = l.source_id)) "
        "SELECT l.source_id, l.canon, list(DISTINCT p.season ORDER BY p.season) "
        "FILTER (WHERE p.season IS NOT NULL) AS seasons FROM l LEFT JOIN p USING (source_id) "
        "GROUP BY l.source_id, l.canon"
    )


def legacy_seasons(
    con: duckdb.DuckDBPyConnection, kind: str, source_id: str | None = None
) -> dict[str, tuple[int, set[int]]]:
    """{legacy source_id: (canonical id, proven seasons)}; an empty set = no proof."""
    sql = _proof_sql(kind).format(extra="AND i.source_id = ?" if source_id is not None else "")
    rows = con.execute(sql, [str(source_id)] if source_id is not None else []).fetchall()
    return {sid: (canon, set(seasons or [])) for sid, canon, seasons in rows}


def legacy_conflicts(
    con: duckdb.DuckDBPyConnection, kind: str, source_id: str | None = None
) -> dict[str, dict[int, int]]:
    """{legacy source_id: {season: rows}} of dependent rows that carry the same raw FPL id but a
    DIFFERENT canonical reference than the legacy row (a matching row elsewhere does not
    cancel them). Seasons that already have their own `fpl-<season>` row for the raw id are
    legitimate reuse and are not conflicts. Matches: a legacy fixture id cited by points rows of
    another season is already a multi-season proof (see `legacy_seasons`)."""
    if kind == "match":
        return {}
    table, canon, _ = KINDS[kind]
    dep = _dependents(kind)
    sql = (
        f"SELECT l.source_id, d.season, count(*) FROM (SELECT i.source_id, i.{canon} AS canon "
        f"FROM {table} i WHERE i.source = '{LEGACY}' {{extra}}) l JOIN ({dep}) d "
        "ON d.raw_id = l.source_id AND d.canon IS NOT NULL AND d.canon <> l.canon "
        f"WHERE NOT EXISTS (SELECT 1 FROM {table} n WHERE "
        f"n.source = '{LEGACY}-' || d.season::VARCHAR AND n.source_id = l.source_id) "
        "GROUP BY l.source_id, d.season"
    ).format(extra="AND i.source_id = ?" if source_id is not None else "")
    out: dict[str, dict[int, int]] = {}
    for sid, season, n in con.execute(
        sql, [str(source_id)] if source_id is not None else []
    ).fetchall():
        out.setdefault(sid, {})[season] = n
    return out


def _check_resolvable(kind: str, season: int, sid: str, seasons: set[int],
                      conflicts: dict[int, int]) -> None:  # fmt: skip
    """Raise only when the unresolved evidence concerns `season`: an unrelated season's
    ambiguity does not block it."""
    if len(seasons) > 1 and season in seasons:
        raise IdentityError(
            f"legacy fpl {kind} id {sid} is proven for seasons {sorted(seasons)}; "
            "run the migration preflight"
        )
    if season in conflicts:
        raise IdentityError(
            f"legacy fpl {kind} id {sid}: {conflicts[season]} season-{season} rows name a "
            "different canonical reference; run the migration preflight"
        )


def source_for(con: duckdb.DuckDBPyConnection, kind: str, season: int, source_id: Any) -> str:
    """The label under which `source_id` of `season` is (or will be) stored: the season's own
    when it exists or nothing else proves it, the legacy one only when it is proven for exactly
    this season. Ambiguous or contradictory evidence about this season is an error."""
    table = KINDS[kind][0]
    sid = str(source_id)
    own = season_source(season)
    if con.execute(
        f"SELECT 1 FROM {table} WHERE source = ? AND source_id = ?", [own, sid]
    ).fetchone():
        return own
    legacy = legacy_seasons(con, kind, sid).get(sid)
    if legacy is None:
        return own
    _check_resolvable(kind, season, sid, legacy[1], legacy_conflicts(con, kind, sid).get(sid, {}))
    return LEGACY if legacy[1] == {season} else own


def season_map(con: duckdb.DuckDBPyConnection, kind: str, season: int) -> dict[int, int]:
    """{raw FPL id: canonical id} for one season: its own label plus legacy rows proven for it."""
    table, canon, _ = KINDS[kind]
    out = {
        int(sid): c
        for sid, c in con.execute(
            f"SELECT source_id, {canon} FROM {table} WHERE source = ?", [season_source(season)]
        ).fetchall()
    }
    conflicts = legacy_conflicts(con, kind)
    for sid, (c, seasons) in legacy_seasons(con, kind).items():
        _check_resolvable(kind, season, sid, seasons, conflicts.get(sid, {}))
        if seasons == {season}:
            out.setdefault(int(sid), c)
    return out


CODE_SOURCE = "fpl"  # player_codes.source for FPL's per-person `code`


class FplIdentity:
    """Resolves one season's live FPL ids; ingest_fpl writes through this.

    A person is linked across seasons only by FPL's stable per-person `code`, recorded in
    `player_codes`. Names, clubs and positions are never evidence: they change (transfers,
    re-registered positions, spelling) and namesakes exist. A record without a usable code, or
    whose code points at a player that already has another FPL id this season, stays a separate
    canonical player and the reason is kept in ingest_state (source 'link')."""

    def __init__(self, db: FootballDB, season: int) -> None:
        self.db, self.season = db, season
        self._taken: set[int] | None = None

    def _exists(self, table: str, source: str, sid: Any) -> bool:
        return bool(self.db.con.execute(
            f"SELECT 1 FROM {table} WHERE source = ? AND source_id = ?", [source, str(sid)]
        ).fetchone())  # fmt: skip

    def team_id(self, source_id: Any, name: str) -> int:
        src = source_for(self.db.con, "team", self.season, source_id)
        return self.db.team_id(src, source_id, name)

    def match_id(self, source_id: Any, *args: Any, **fields: Any) -> int:
        src = source_for(self.db.con, "match", self.season, source_id)
        return self.db.match_id(src, source_id, *args, provenance=LEGACY, **fields)

    def _flag(self, key: str, reason: str) -> None:
        """Record why a code or player is disputed. Earlier reasons are kept, never replaced."""
        row = self.db.con.execute(
            "SELECT detail FROM ingest_state WHERE source = 'link' AND key = ?", [key]
        ).fetchone()
        if row and reason in (row[0] or "").split("; "):
            return
        detail = f"{row[0]}; {reason}" if row and row[0] else reason
        self.db.con.execute(
            "INSERT OR REPLACE INTO ingest_state VALUES ('link', ?, 'unlinked', ?, ?)",
            [key, detail, now()],
        )

    def _flagged(self, key: str) -> bool:
        return bool(self.db.con.execute(
            "SELECT 1 FROM ingest_state WHERE source = 'link' AND key = ?", [key]
        ).fetchone())  # fmt: skip

    def _code_owner(self, code: str) -> int | None:
        row = self.db.con.execute(
            "SELECT player_id FROM player_codes WHERE source = ? AND code = ?", [CODE_SOURCE, code]
        ).fetchone()
        return row[0] if row else None

    def _codes_of(self, pid: int) -> set[str]:
        return {r[0] for r in self.db.con.execute(
            "SELECT code FROM player_codes WHERE source = ? AND player_id = ?", [CODE_SOURCE, pid]
        ).fetchall()}  # fmt: skip

    def _blocked(self, code: str, pid: int | None = None) -> str | None:
        """Why `code` (or the player it would attach to) is disputed evidence, else None. A
        flagged code, a flagged player, or a player recorded with several codes never drives a
        link or a new code record; the recorded rows themselves are kept as evidence."""
        if self._flagged(f"fpl-code:{code}"):
            return f"code {code} is disputed"
        for p in {x for x in (self._code_owner(code), pid) if x is not None}:
            if self._flagged(f"fpl-player:{p}"):
                return f"player {p} is disputed"
            if len(self._codes_of(p) | ({code} if p == pid else set())) > 1:
                return f"player {p} is recorded with several codes"
        return None

    def _register(self, code: str, pid: int, evidence: str) -> None:
        self.db.con.execute(
            "INSERT INTO player_codes VALUES (?, ?, ?, ?, ?, ?)",
            [CODE_SOURCE, code, pid, evidence, self.season, now()],
        )

    def seed_codes(self) -> dict[str, int]:
        """Record the codes already stored in `fpl_players_gw.raw`. A code seen with several
        canonical players, or a player seen with several codes, is evidence of a past wrong merge
        or split: neither is trusted and it is flagged instead of recorded."""
        rows = self.db.con.execute(
            "SELECT json_extract_string(raw, '$.code') AS code, player_id, min(season) "
            "FROM fpl_players_gw WHERE player_id IS NOT NULL AND "
            "json_extract_string(raw, '$.code') IS NOT NULL GROUP BY 1, 2"
        ).fetchall()
        by_code: dict[str, list[tuple[int, int]]] = {}
        by_player: dict[int, set[str]] = {}
        for code, pid, first in rows:
            by_code.setdefault(code, []).append((pid, first))
            by_player.setdefault(pid, set()).add(code)
        out = {"recorded": 0, "flagged": 0}
        for code, owners in by_code.items():
            pids = [pid for pid, _ in owners]
            if len(pids) > 1:
                self._flag(f"fpl-code:{code}", f"code seen with players {sorted(pids)}")
                out["flagged"] += 1
            elif len(by_player[pids[0]]) > 1:
                self._flag(
                    f"fpl-player:{pids[0]}", f"player seen with codes {sorted(by_player[pids[0]])}"
                )
                out["flagged"] += 1
            elif self._blocked(code, pids[0]):
                out["flagged"] += 1  # disputed earlier: the existing flag stays authoritative
            elif self._code_owner(code) is None:
                self.db.con.execute(
                    "INSERT INTO player_codes VALUES (?, ?, ?, 'snapshot raw', ?, ?)",
                    [CODE_SOURCE, code, pids[0], owners[0][1], now()],
                )
                out["recorded"] += 1
            elif self._code_owner(code) != pids[0]:
                self._flag(f"fpl-code:{code}", f"recorded for player {self._code_owner(code)}, "
                           f"snapshots say {pids[0]}")  # fmt: skip
                out["flagged"] += 1
        return out

    def player_id(self, source_id: Any, name: str, position: str | None, code: Any = None) -> int:
        """Existing mapping, else a new `fpl-<season>` one that joins an existing canonical
        player only through a recorded `code` (and only if that player has no other FPL id this
        season)."""
        src = source_for(self.db.con, "player", self.season, source_id)
        code = str(code) if code not in (None, "") else None
        if self._taken is None:
            self._taken = set(season_map(self.db.con, "player", self.season).values())
        if self._exists("player_ids", src, source_id):
            pid = self.db.player_id(src, source_id, name, position)
            if code is not None:
                owner, known = self._code_owner(code), self._codes_of(pid)
                if owner is not None and owner != pid:
                    self._flag(f"fpl-code:{code}", f"recorded for player {owner}, but "
                               f"{src}:{source_id} maps to {pid}")  # fmt: skip
                elif owner is None and known:
                    self._flag(f"fpl-player:{pid}", f"recorded with codes {sorted(known)}, "
                               f"{src}:{source_id} says {code}")  # fmt: skip
                elif owner is None and not self._blocked(code, pid):
                    self._register(code, pid, "bootstrap")
            return pid
        link, reason = None, None
        owner = self._code_owner(code) if code is not None else None
        blocked = self._blocked(code) if code is not None else None
        if code is None:
            reason = "no stable code supplied"
        elif blocked:
            reason = f"{blocked}; kept separate"
        elif owner is not None and owner in self._taken:
            reason = f"code {code} belongs to player {owner}, who already has another FPL id"
        else:
            link = owner
        pid = self.db.player_id(src, source_id, name, position, link_to=link)
        if reason:
            self._flag(f"{src}:{source_id}", reason)
        elif code is not None and owner is None:
            self._register(code, pid, "bootstrap")
        self._taken.add(pid)
        return pid


# ---- migration -------------------------------------------------------------------------


@dataclass
class Plan:
    """Legacy rows to relabel: kind -> {source_id: (canonical, source_name, season)}."""

    rows: dict[str, dict[str, tuple[int, str | None, int]]] = field(default_factory=dict)
    problems: list[str] = field(default_factory=list)
    counts: dict[str, int] = field(default_factory=dict)
    other_sources: dict[str, dict[str, int]] = field(default_factory=dict)


def preflight(con: duckdb.DuckDBPyConnection) -> Plan:
    """Read-only. Every legacy row must be proven to exactly one season, point at an existing
    canonical row, and not collide with an existing `fpl-<season>` row."""
    plan = Plan()
    for kind, (table, canon, named) in KINDS.items():
        parent, pcol = PARENTS[kind]
        names = (
            dict(con.execute(f"SELECT source_id, source_name FROM {table} WHERE source = ?",
                             [LEGACY]).fetchall())
            if named else {}
        )  # fmt: skip
        existing = {
            r[0] for r in con.execute(
                f"SELECT source || ':' || source_id FROM {table} WHERE regexp_matches(source, ?)",
                [rf"^{LEGACY}-[0-9]+$"],
            ).fetchall()
        }  # fmt: skip
        parents = {r[0] for r in con.execute(f"SELECT {pcol} FROM {parent}").fetchall()}
        rows: dict[str, tuple[int, str | None, int]] = {}
        conflicts = legacy_conflicts(con, kind)
        for sid, (c, seasons) in sorted(legacy_seasons(con, kind).items()):
            if len(seasons) != 1:
                what = "no proof of season" if not seasons else f"proven for {sorted(seasons)}"
                own = sorted(e.split(":")[0] for e in existing if e.endswith(f":{sid}"))
                if own:
                    what += f" ({', '.join(own)} row exists for this id)"
                plan.problems.append(f"{table} {sid}: {what}")
            elif conflicts.get(sid):
                plan.problems.append(
                    f"{table} {sid}: conflicting evidence, rows naming another canonical "
                    f"reference by season {conflicts[sid]}"
                )
            elif c not in parents:
                plan.problems.append(f"{table} {sid}: canonical {canon} {c} has no {parent} row")
            elif f"{season_source(next(iter(seasons)))}:{sid}" in existing:
                plan.problems.append(
                    f"{table} {sid}: {season_source(next(iter(seasons)))} row already exists"
                )
            else:
                rows[sid] = (c, names.get(sid), next(iter(seasons)))
        plan.rows[kind] = rows
        plan.counts[table] = len(rows)
        plan.other_sources[table] = dict(con.execute(
            f"SELECT source, count(*) FROM {table} WHERE source <> ? "
            "AND NOT regexp_matches(source, ?) GROUP BY source ORDER BY source",
            [LEGACY, rf"^{LEGACY}-[0-9]+$"],
        ).fetchall())  # fmt: skip
    return plan


def _journal_exists(con: duckdb.DuckDBPyConnection) -> bool:
    return bool(con.execute(
        "SELECT 1 FROM information_schema.tables WHERE table_name = ?", [JOURNAL]
    ).fetchone())  # fmt: skip


def _legacy_count(con: duckdb.DuckDBPyConnection) -> int:
    return sum(
        con.execute(f"SELECT count(*) FROM {t} WHERE source = ?", [LEGACY]).fetchone()[0]
        for t, _, _ in KINDS.values()
    )


def _active_entries(con: duckdb.DuckDBPyConnection, migration_id: str | None = None) -> list[tuple]:
    if not _journal_exists(con):
        return []
    sql = (f"SELECT migration_id, table_name, source_id, canonical_id, source_name, old_source, "
           f"new_source FROM {JOURNAL} WHERE rolled_back_at IS NULL")  # fmt: skip
    params: list[Any] = []
    if migration_id:
        sql += " AND migration_id = ?"
        params.append(migration_id)
    return con.execute(sql + " ORDER BY table_name, source_id", params).fetchall()


def _present(con: duckdb.DuckDBPyConnection, entries: list[tuple], where: str) -> list[str]:
    """Journal entries whose `where` ('new' or 'old') row is missing or differs from the journal
    in any recorded field: the canonical reference and, where the table has one, the source
    name (None equals only None)."""
    bad = []
    by_table = {t: (c, named) for t, c, named in KINDS.values()}
    for _, table, sid, canon_id, name, old, new in entries:
        label = new if where == "new" else old
        canon, named = by_table[table]
        row = con.execute(
            f"SELECT {canon}" + (", source_name" if named else "")
            + f" FROM {table} WHERE source = ? AND source_id = ?",
            [label, sid],
        ).fetchone()  # fmt: skip
        if row is None or row[0] != canon_id or (named and row[1] != name):
            bad.append(f"{table} {label}:{sid}")
    return bad


def _verify(con: duckdb.DuckDBPyConnection, plan: Plan) -> None:
    """Inside the migration transaction, before COMMIT."""
    for kind, (table, canon, named) in KINDS.items():
        for sid, (c, name, season) in plan.rows[kind].items():
            sel = f"SELECT {canon}" + (", source_name" if named else "")
            got = con.execute(f"{sel} FROM {table} WHERE source = ? AND source_id = ?",
                              [season_source(season), sid]).fetchone()  # fmt: skip
            if got is None or got[0] != c or (named and got[1] != name):
                raise IdentityError(f"{table} {sid}: relabelled row differs from the original")
        left = con.execute(f"SELECT count(*) FROM {table} WHERE source = ?", [LEGACY]).fetchone()[0]
        if left:
            raise IdentityError(f"{table}: {left} legacy rows left after relabelling")


def migrate(con: duckdb.DuckDBPyConnection) -> dict[str, Any]:
    """Relabel proven legacy `fpl` identity rows to `fpl-<season>`, one transaction.

    Statuses: 'migrated'; 'noop-verified' (nothing legacy left and every journalled row is
    present as recorded); 'nothing-to-migrate' (nothing legacy, no journal). Refuses, writing
    nothing, on missing/ambiguous proof, a bad canonical reference, an existing target row, or
    legacy rows left with a journal that says they were already migrated."""
    if _legacy_count(con) == 0:
        entries = _active_entries(con)
        if not entries:
            return {"status": "nothing-to-migrate", "rows": 0}
        bad = _present(con, entries, "new")
        if bad:
            raise MigrationRefused([f"journalled row missing or changed: {b}" for b in bad[:20]])
        return {"status": "noop-verified", "rows": len(entries)}
    if _active_entries(con):
        raise MigrationRefused(["legacy fpl rows reappeared after a completed migration"])
    migration_id = f"{MIGRATION}:{now().strftime('%Y%m%dT%H%M%S%f')}"
    con.execute("BEGIN TRANSACTION")
    try:
        plan = preflight(con)  # asserted again inside the transaction
        if plan.problems:
            raise MigrationRefused(plan.problems[:50])
        con.execute(_JOURNAL_DDL)
        at = now()
        for kind, (table, canon, named) in KINDS.items():
            for sid, (c, name, season) in plan.rows[kind].items():
                new = season_source(season)
                cols, marks = (f"source, source_id, {canon}", "?, ?, ?")
                vals: list[Any] = [new, sid, c]
                if named:
                    cols, marks, vals = cols + ", source_name", marks + ", ?", [*vals, name]
                con.execute(f"INSERT INTO {table} ({cols}) VALUES ({marks})", vals)
                con.execute(
                    f"INSERT INTO {JOURNAL} VALUES (?, ?, ?, ?, ?, ?, ?, ?, NULL)",
                    [migration_id, table, sid, c, name, LEGACY, new, at],
                )
            con.execute(f"DELETE FROM {table} WHERE source = ?", [LEGACY])
        _verify(con, plan)
        con.execute("COMMIT")
    except BaseException:
        con.execute("ROLLBACK")
        raise
    return {"status": "migrated", "migration_id": migration_id, "rows": sum(plan.counts.values()),
            "counts": plan.counts}  # fmt: skip


def rollback(con: duckdb.DuckDBPyConnection) -> dict[str, Any]:
    """Undo the active migration from its journal: restore the legacy rows (same canonical id
    and source name), remove the relabelled ones. Refuses if a journalled row has since changed
    or a legacy row exists."""
    entries = _active_entries(con)
    if not entries:
        return {"status": "nothing-to-roll-back", "rows": 0}
    ids = {e[0] for e in entries}
    if len(ids) != 1:
        raise MigrationRefused([f"several active migrations: {sorted(ids)}"])
    con.execute("BEGIN TRANSACTION")
    try:
        problems = [
            f"journalled row missing or changed: {b}" for b in _present(con, entries, "new")
        ]
        if _legacy_count(con):
            problems.append("legacy fpl rows exist; cannot restore over them")
        if problems:
            raise MigrationRefused(problems[:50])
        canon_of = {t: (c, n) for t, c, n in KINDS.values()}
        for _mid, table, sid, canon_id, name, old, new in entries:
            canon, named = canon_of[table]
            cols, marks, vals = f"source, source_id, {canon}", "?, ?, ?", [old, sid, canon_id]
            if named:
                cols, marks, vals = cols + ", source_name", marks + ", ?", [*vals, name]
            con.execute(f"INSERT INTO {table} ({cols}) VALUES ({marks})", vals)
            con.execute(f"DELETE FROM {table} WHERE source = ? AND source_id = ?", [new, sid])
        con.execute(f"UPDATE {JOURNAL} SET rolled_back_at = ? WHERE migration_id = ? "
                    "AND rolled_back_at IS NULL", [now(), next(iter(ids))])  # fmt: skip
        left = [
            e
            for e in entries
            if con.execute(
                f"SELECT 1 FROM {e[1]} WHERE source = ? AND source_id = ?", [e[6], e[2]]
            ).fetchone()
        ]
        if left or _present(con, entries, "old"):
            raise IdentityError("rollback left the table different from the journal")
        con.execute("COMMIT")
    except BaseException:
        con.execute("ROLLBACK")
        raise
    return {"status": "rolled-back", "migration_id": next(iter(ids)), "rows": len(entries)}
