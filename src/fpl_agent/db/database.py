"""DuckDB wrapper: init, canonical id resolution, parquet export/import."""

from __future__ import annotations

import hashlib
import json
import shutil
from contextlib import contextmanager
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import duckdb
import pandas as pd

from fpl_agent.data.id_map import normalise
from fpl_agent.db.schema import TABLES

EXPORT_VERSION = 1
MANIFEST = "manifest.json"


class ExportError(Exception):
    """An export that is missing, incomplete or does not match its manifest."""


def _sql_path(path: Path) -> str:
    """A path as a DuckDB string literal (COPY ... TO takes no parameters): quotes doubled."""
    return "'" + str(path).replace("'", "''") + "'"


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def verify_export(directory: str | Path) -> list[str]:
    """Problems with an export folder (empty = it is complete and matches its manifest): every
    table file present, unchanged (sha256) and holding the recorded number of rows."""
    d = Path(directory)
    try:
        manifest = json.loads((d / MANIFEST).read_text(encoding="utf-8"))
    except FileNotFoundError:
        return [f"no {MANIFEST} (missing or legacy export)"]
    except ValueError:
        return [f"{MANIFEST} is not valid JSON"]
    if manifest.get("version") != EXPORT_VERSION:
        return [f"unknown export version {manifest.get('version')!r}"]
    problems = []
    con = duckdb.connect()
    try:
        for name, meta in (manifest.get("tables") or {}).items():
            f = d / f"{name}.parquet"
            if not f.exists():
                problems.append(f"{name}: file missing")
                continue
            if _sha256(f) != meta.get("sha256"):
                problems.append(f"{name}: checksum differs")
                continue
            n = con.execute("SELECT count(*) FROM read_parquet(?)", [str(f)]).fetchone()[0]
            if n != meta.get("rows"):
                problems.append(f"{name}: {n} rows, manifest says {meta.get('rows')}")
    finally:
        con.close()
    if not manifest.get("tables"):
        problems.append("manifest lists no tables")
    return problems


# Clubs are named differently by every source; canonical team key after normalise().
TEAM_ALIASES = {
    "manchester city": "man city",
    "manchester united": "man utd",
    "man united": "man utd",
    "newcastle united": "newcastle",
    "wolverhampton wanderers": "wolves",
    "wolverhampton": "wolves",
    "tottenham": "spurs",
    "tottenham hotspur": "spurs",
    "nottingham forest": "nott m forest",
    "nottm forest": "nott m forest",
    "brighton and hove albion": "brighton",
    "brighton hove albion": "brighton",
    "west bromwich albion": "west brom",
    "sheffield united": "sheffield utd",
    "sheffield wednesday": "sheffield wed",
    "sheffield weds": "sheffield wed",
    "nott m forest": "nott m forest",
    "leeds united": "leeds",
    "leicester city": "leicester",
    "norwich city": "norwich",
    "ipswich town": "ipswich",
    "hull city": "hull",
    "coventry city": "coventry",
    "stoke city": "stoke",
    "swansea city": "swansea",
    "cardiff city": "cardiff",
    "queens park rangers": "qpr",
    "preston north end": "preston",
    "derby county": "derby",
    "birmingham city": "birmingham",
    "blackburn rovers": "blackburn",
    "bristol city": "bristol city",
    "luton town": "luton",
    "huddersfield town": "huddersfield",
    "plymouth argyle": "plymouth",
    "oxford united": "oxford",
    "bristol rvs": "bristol rovers",
    "peterboro": "peterborough",
    "peterborough united": "peterborough",
    "west ham united": "west ham",
    "afc bournemouth": "bournemouth",
    "bournemouth afc": "bournemouth",
}


def team_key(name: str) -> str:
    n = normalise(name)
    n = n[:-3] if n.endswith(" fc") else n
    return TEAM_ALIASES.get(n, n)


# Words clubs add or drop between sources ("Bolton" vs "Bolton Wanderers", "Lincoln City").
GENERIC_TOKENS = {"city", "town", "athletic", "wanderers", "rovers", "county", "albion", "united",
                  "hotspur", "fc", "afc", "cf", "sc", "rangers", "argyle", "orient"}  # fmt: skip


# Neighbouring clubs whose names differ only by a generic word; never merge them with each other.
DISTINCT_CLUBS = {frozenset({"dundee", "dundee united"})}


def _same_club(a: str, b: str) -> bool:
    """Equal once generic club words are ignored, and only when that leaves a name behind."""
    ta, tb = set(a.split()), set(b.split())
    if ta == tb:
        return True
    if frozenset({a, b}) in DISTINCT_CLUBS:
        return False
    ca, cb = ta - GENERIC_TOKENS, tb - GENERIC_TOKENS
    return bool(ca) and ca == cb and (ta <= tb or tb <= ta)


def now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


class FootballDB:
    def __init__(self, path: str | Path = ":memory:") -> None:
        if str(path) != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.con = duckdb.connect(str(path))
        for name, ddl in TABLES.items():
            self.con.execute(f"CREATE TABLE IF NOT EXISTS {name} ({ddl})")

    @contextmanager
    def transaction(self):
        """All-or-nothing writes: a source that fails half way leaves no partial rows."""
        self.con.execute("BEGIN TRANSACTION")
        try:
            yield
        except BaseException:
            self.con.execute("ROLLBACK")
            raise
        self.con.execute("COMMIT")

    def close(self) -> None:
        self.con.close()

    # ---- generic helpers -------------------------------------------------
    def upsert(self, table: str, df: pd.DataFrame) -> int:
        """INSERT OR REPLACE a frame (columns must be a subset of the table's columns)."""
        if df.empty:
            return 0
        cols = ", ".join(df.columns)
        self.con.register("_df", df)
        try:
            self.con.execute(f"INSERT OR REPLACE INTO {table} ({cols}) SELECT {cols} FROM _df")
        finally:
            self.con.unregister("_df")
        return len(df)

    def log_odds(self, df: pd.DataFrame) -> int:
        """Append odds observations to match_odds_log, skipping any whose prices equal the
        newest already logged for that match/source/bookmaker/closing (re-reading the same
        file adds nothing; a price change is a new observation)."""
        if df.empty:
            return 0
        price = ("home", "draw", "away", "over25", "under25")
        self.con.register("_odds", df)
        try:
            n = self.con.execute(f"""
                INSERT INTO match_odds_log ({", ".join(df.columns)})
                SELECT {", ".join("o." + c for c in df.columns)} FROM _odds o
                LEFT JOIN (SELECT * FROM match_odds_log QUALIFY row_number() OVER (
                    PARTITION BY match_id, source, bookmaker, closing
                    ORDER BY ingested_at DESC) = 1) l
                  USING (match_id, source, bookmaker, closing)
                WHERE l.match_id IS NULL OR {" OR ".join(
                    f"o.{c} IS DISTINCT FROM l.{c}" for c in price)}
            """).fetchone()  # fmt: skip
        finally:
            self.con.unregister("_odds")
        return int(n[0]) if n else 0

    def append(self, table: str, df: pd.DataFrame) -> int:
        if df.empty:
            return 0
        cols = ", ".join(df.columns)
        self.con.register("_df", df)
        try:
            self.con.execute(f"INSERT INTO {table} ({cols}) SELECT {cols} FROM _df")
        finally:
            self.con.unregister("_df")
        return len(df)

    def query(self, sql: str, params: list[Any] | None = None) -> pd.DataFrame:
        return self.con.execute(sql, params or []).df()

    def log(self, source: str, table: str, rows: int, note: str = "") -> None:
        self.con.execute(
            "INSERT INTO ingest_log VALUES (?, ?, ?, ?, ?)", [source, now(), table, rows, note]
        )

    def _next_id(self, table: str, col: str) -> int:
        return self.con.execute(f"SELECT coalesce(max({col}), 0) + 1 FROM {table}").fetchone()[0]

    # ---- canonical ids ---------------------------------------------------
    def team_id(self, source: str, source_id: str | int, name: str) -> int:
        sid = str(source_id)
        row = self.con.execute(
            "SELECT team_id FROM team_ids WHERE source = ? AND source_id = ?", [source, sid]
        ).fetchone()
        if row:
            return row[0]
        key = team_key(name)
        row = self.con.execute("SELECT team_id FROM teams WHERE key = ?", [key]).fetchone()
        if not row:
            for tid_, k in self.con.execute("SELECT team_id, key FROM teams").fetchall():
                if _same_club(key, k):
                    row = (tid_,)
                    break
        if row:
            tid = row[0]
        else:
            tid = self._next_id("teams", "team_id")
            self.con.execute("INSERT INTO teams VALUES (?, ?, ?)", [tid, name, key])
        self.con.execute("INSERT INTO team_ids VALUES (?, ?, ?, ?)", [source, sid, tid, name])
        return tid

    def name_link(self, candidates: list[int], name: str, position: str | None,
                  source: str) -> int | None:  # fmt: skip
        """The canonical player a new `source` player may be linked to by name, or None.

        A link needs all of: a full name of two or more words, exactly one canonical player with
        that name, a stored position that agrees (or is unknown on either side), and no other
        id from the same source already linked to that player (two namesakes in one source are
        two people). Otherwise the player stays unlinked, a second row rather than a wrong
        merge, and the reason is kept in ingest_state (source 'link') for review."""
        key = normalise(name)
        reason = None
        if len(key.split()) < 2:
            reason = "single-word name"
        elif len(candidates) != 1:
            reason = f"{len(candidates)} players share the name" if candidates else None
        else:
            pid = candidates[0]
            row = self.con.execute("SELECT position FROM players WHERE player_id = ?",
                                   [pid]).fetchone()  # fmt: skip
            known = row[0] if row else None
            taken = self.con.execute(
                "SELECT 1 FROM player_ids WHERE player_id = ? AND source = ? LIMIT 1",
                [pid, source],
            ).fetchone()
            if position and known and position != known:
                reason = f"position {position} differs from {known}"
            elif taken:
                reason = f"player {pid} is already linked to another {source} id"
            else:
                return pid
        if reason:
            self.con.execute(
                "INSERT OR REPLACE INTO ingest_state VALUES ('link', ?, 'unlinked', ?, ?)",
                [f"{source}:{key}", reason, now()],
            )
        return None

    def player_id(
        self,
        source: str,
        source_id: str | int,
        name: str,
        position: str | None = None,
        link_to: int | None = None,
    ) -> int:
        """Resolve a source player to the canonical id. `link_to` attaches it to an existing one."""
        sid = str(source_id)
        row = self.con.execute(
            "SELECT player_id FROM player_ids WHERE source = ? AND source_id = ?", [source, sid]
        ).fetchone()
        if row:
            return row[0]
        pid = link_to
        if pid is None:
            pid = self._next_id("players", "player_id")
            self.con.execute("INSERT INTO players VALUES (?, ?, NULL, ?)", [pid, name, position])
        self.con.execute("INSERT INTO player_ids VALUES (?, ?, ?, ?)", [source, sid, pid, name])
        return pid

    def match_id(
        self,
        source: str,
        source_id: str | int | None,
        competition_id: str,
        season: int,
        match_date: date,
        home_team_id: int,
        away_team_id: int,
        allow_repeat: bool = False,
        **fields: Any,
    ) -> int:
        """Find or create a league match by (competition, season, home, away).

        Dates move (postponements, TV picks), so they are updated, never used as the key. A
        cup competition can repeat a pairing (two legs, knockout rounds), so with `allow_repeat`
        the date is part of the key and a new source id never merges into an earlier leg.
        """
        if source_id is not None:
            row = self.con.execute(
                "SELECT match_id FROM match_ids WHERE source = ? AND source_id = ?",
                [source, str(source_id)],
            ).fetchone()
            if row:
                self._update_match(row[0], {"match_date": match_date, **fields})
                return row[0]
        sql = (
            "SELECT match_id FROM matches WHERE competition_id = ? AND season = ? "
            "AND home_team_id = ? AND away_team_id = ?"
        )
        params: list[Any] = [competition_id, season, home_team_id, away_team_id]
        if allow_repeat:
            sql += " AND match_date = ?"
            params.append(match_date)
        # a league pairing happens once per season, so a moved date can't duplicate
        row = self.con.execute(sql, params).fetchone()
        if row:
            mid = row[0]
            self._update_match(mid, {"match_date": match_date, **fields})
        else:
            mid = self._next_id("matches", "match_id")
            cols = {
                "match_id": mid,
                "competition_id": competition_id,
                "season": season,
                "match_date": match_date,
                "home_team_id": home_team_id,
                "away_team_id": away_team_id,
                "source": source,
                "ingested_at": now(),
                **fields,
            }
            names = ", ".join(cols)
            marks = ", ".join("?" for _ in cols)
            self.con.execute(f"INSERT INTO matches ({names}) VALUES ({marks})", list(cols.values()))
        if source_id is not None:
            self.con.execute(
                "INSERT OR REPLACE INTO match_ids VALUES (?, ?, ?)", [source, str(source_id), mid]
            )
        return mid

    def _update_match(self, mid: int, fields: dict[str, Any]) -> None:
        fields = {k: v for k, v in fields.items() if v is not None}
        if not fields:
            return
        sets = ", ".join(f"{k} = ?" for k in fields)
        self.con.execute(f"UPDATE matches SET {sets} WHERE match_id = ?", [*fields.values(), mid])

    # ---- point-in-time view ---------------------------------------------
    def fpl_players_as_of(self, season: int, gw: int, as_of: datetime) -> pd.DataFrame:
        """Latest FPL player rows for a GW taken at or before `as_of` (no look-ahead)."""
        return self.query(
            "SELECT * FROM fpl_players_gw WHERE season = ? AND gw = ? AND snapshot_at <= ? "
            "QUALIFY snapshot_at = max(snapshot_at) OVER (PARTITION BY fpl_id)",
            [season, gw, as_of],
        )

    # ---- parquet (the `data` release stores these) -----------------------
    def export_parquet(self, directory: str | Path, keep: int = 2) -> dict[str, int]:
        """Every table (empty ones too, so an emptied table stays empty) written to a fresh
        staging folder with a manifest of row counts, columns and checksums, checked, and only
        then swapped in for `directory`. The export it replaces is kept as
        `<directory>.prev-<stamp>` (the newest `keep` of those), so a bad run can be rolled
        back and an old folder can never leak stale tables into a new export."""
        out = Path(directory)
        out.parent.mkdir(parents=True, exist_ok=True)
        stamp = now().strftime("%Y%m%dT%H%M%S%f")
        stage = out.with_name(f"{out.name}.staging-{stamp}")
        stage.mkdir()
        try:
            counts, tables = {}, {}
            for name in TABLES:
                counts[name] = self.con.execute(f"SELECT count(*) FROM {name}").fetchone()[0]
                target = stage / f"{name}.parquet"
                self.con.execute(f"COPY (SELECT * FROM {name}) TO {_sql_path(target)} "
                                 "(FORMAT parquet, COMPRESSION zstd)")  # fmt: skip
                cols = [[c[0], c[1]] for c in self.con.execute(f"DESCRIBE {name}").fetchall()]
                tables[name] = {"rows": counts[name], "columns": cols, "sha256": _sha256(target)}
            manifest = {"version": EXPORT_VERSION, "created_at": now().isoformat() + "Z",
                        "tables": tables}  # fmt: skip
            (stage / MANIFEST).write_text(json.dumps(manifest, indent=1), encoding="utf-8")
            problems = verify_export(stage)
            if problems:
                raise ExportError("; ".join(problems))
            if out.exists():
                out.rename(out.with_name(f"{out.name}.prev-{stamp}"))
            stage.rename(out)
        except BaseException:
            shutil.rmtree(stage, ignore_errors=True)
            raise
        old = sorted(out.parent.glob(f"{out.name}.prev-*"))
        for gone in old[: max(0, len(old) - keep)]:
            shutil.rmtree(gone, ignore_errors=True)
        return counts

    def import_parquet(self, directory: str | Path, require_manifest: bool = False) -> int:
        """Load an export. One with a manifest must match it exactly or nothing is loaded; a
        legacy export without one is accepted unless `require_manifest`. Tables with a primary
        key are upserted; the append-only ones (decisions, projections, reviews, logs) only
        gain rows they do not already hold, so importing the same export twice adds nothing.
        An export already imported (same manifest) is skipped. Returns the tables loaded."""
        d = Path(directory)
        key = None
        if (d / MANIFEST).exists():
            problems = verify_export(d)
            if problems:
                raise ExportError(f"{d.name}: " + "; ".join(problems))
            key = _sha256(d / MANIFEST)
            if self.con.execute(
                "SELECT 1 FROM ingest_state WHERE source = 'import' AND key = ?", [key]
            ).fetchone():
                return 0
        elif require_manifest:
            raise ExportError(f"{d.name}: no {MANIFEST}")
        loaded = 0
        with self.transaction():
            for name, ddl in TABLES.items():
                f = d / f"{name}.parquet"
                if not f.exists():
                    continue
                if "PRIMARY KEY" in ddl:
                    self.con.execute(
                        f"INSERT OR REPLACE INTO {name} SELECT * FROM read_parquet(?)", [str(f)]
                    )
                else:
                    self.con.execute(f"INSERT INTO {name} SELECT * FROM read_parquet(?) "
                                     f"EXCEPT SELECT * FROM {name}", [str(f)])  # fmt: skip
                loaded += 1
            if key:
                self.con.execute(
                    "INSERT OR REPLACE INTO ingest_state VALUES ('import', ?, 'done', ?, ?)",
                    [key, str(loaded), now()],
                )
        return loaded

    def counts(self) -> dict[str, int]:
        return {
            n: self.con.execute(f"SELECT count(*) FROM {n}").fetchone()[0] for n in TABLES
        }  # fmt: skip
