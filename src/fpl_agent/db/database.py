"""DuckDB wrapper: init, canonical id resolution, parquet export/import."""

from __future__ import annotations

from contextlib import contextmanager
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import duckdb
import pandas as pd

from fpl_agent.data.id_map import normalise
from fpl_agent.db.schema import TABLES

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

    # ---- parquet (the `data` branch stores these) ------------------------
    def export_parquet(self, directory: str | Path) -> dict[str, int]:
        out = Path(directory)
        out.mkdir(parents=True, exist_ok=True)
        counts = {}
        for name in TABLES:
            n = self.con.execute(f"SELECT count(*) FROM {name}").fetchone()[0]
            counts[name] = n
            if n:
                target = out / f"{name}.parquet"
                self.con.execute(
                    f"COPY (SELECT * FROM {name}) TO '{target}' (FORMAT parquet, COMPRESSION zstd)"
                )
        return counts

    def import_parquet(self, directory: str | Path) -> None:
        for name in TABLES:
            f = Path(directory) / f"{name}.parquet"
            if not f.exists():
                continue
            src = f"SELECT * FROM read_parquet('{f}')"
            try:
                self.con.execute(f"INSERT OR REPLACE INTO {name} {src}")
            except duckdb.BinderException:  # log-style tables have no primary key
                self.con.execute(f"INSERT INTO {name} {src}")

    def counts(self) -> dict[str, int]:
        return {
            n: self.con.execute(f"SELECT count(*) FROM {n}").fetchone()[0] for n in TABLES
        }  # fmt: skip
