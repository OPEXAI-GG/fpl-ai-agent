"""Point-in-time feature frames built from the football database.

Everything here is computed from rows strictly before the row being predicted (`shift` before any
rolling window), so a feature never contains information from the match it is used to predict.
"""

from __future__ import annotations

from datetime import datetime

import numpy as np
import pandas as pd

from fpl_agent.db.database import FootballDB

POSITION_CODE = {"GKP": 0, "DEF": 1, "MID": 2, "FWD": 3}
STAT_FIELDS = {
    "goals": "goals_scored", "assists": "assists", "xg": "expected_goals",
    "xa": "expected_assists", "cbit": "defensive_contribution", "saves": "saves",
    "yellow": "yellow_cards", "red": "red_cards", "own_goals": "own_goals",
    "pen_missed": "penalties_missed", "pen_saved": "penalties_saved",
    "conceded": "goals_conceded", "clean_sheet": "clean_sheets", "starts": "starts",
    "xp": "xP",
}  # fmt: skip


def _json(name: str) -> str:
    return f"TRY_CAST(json_extract_string(f.stats, '$.{name}') AS DOUBLE)"


_SEASON_POSITION = """
    SELECT season, {key}, any_value(position) AS position FROM fpl_players_gw
    WHERE position IS NOT NULL {cutoff} GROUP BY ALL HAVING count(DISTINCT position) = 1"""


_SNAPSHOT_FIELDS = ("team_id", "position", "price", "status", "chance", "position_source",
                    "snapshot_kind")  # fmt: skip
# an FPL bootstrap snapshot stores the whole element (it has element_type); a row rebuilt from
# the vaastav archive stores only was_home, and its snapshot_at is the kickoff, not a moment it
# was observed, and its status is assumed
OBSERVED_SNAPSHOT = "json_extract(raw, '$.element_type') IS NOT NULL"


def _snapshot_join(cutoff: str) -> tuple[str, str]:
    """Columns and joins giving each row `f` (season, gw, fpl_id) its gameweek's latest snapshot
    and the season's agreed position, both limited by `cutoff` (an extra WHERE clause on
    fpl_players_gw). One definition serves the whole-database frame and every cutoff, so the
    two cannot select differently."""
    cols = """s.team_id, coalesce(s.position, sp.position) AS position,
               s.now_cost AS price, s.status, s.chance_next AS chance,
               CASE WHEN s.position IS NOT NULL THEN 'snapshot'
                    WHEN sp.position IS NOT NULL THEN 'season' END AS position_source,
               CASE WHEN s.observed THEN 'observed'
                    WHEN s.snapshot_at IS NOT NULL THEN 'reconstructed' END AS snapshot_kind"""
    joins = f"""
        LEFT JOIN (
            SELECT *, coalesce({OBSERVED_SNAPSHOT}, false) AS observed FROM fpl_players_gw
            WHERE true {cutoff}
            QUALIFY snapshot_at = max(snapshot_at) OVER (PARTITION BY season, gw, fpl_id)
        ) s ON s.season = f.season AND s.gw = f.gw AND s.fpl_id = f.fpl_id
        LEFT JOIN ({_SEASON_POSITION.format(key="fpl_id", cutoff=cutoff)}
        ) sp ON sp.season = f.season AND sp.fpl_id = f.fpl_id"""
    return cols, joins


def snapshot_as_of(frame: pd.DataFrame, db: FootballDB, as_of: datetime) -> pd.DataFrame:
    """`frame` with team, position, price, status, chance and `position_source` re-selected from
    snapshots taken at or before `as_of`, so one frame built from the whole database can serve
    any cutoff (replay, backtest) without a later observation leaking into an earlier one.
    Every row is re-selected, its own gameweek's snapshot included: a late or corrected snapshot
    of that gameweek, or a later disagreeing one elsewhere in the season, cannot change what an
    earlier cutoff sees. A row with no eligible snapshot keeps those fields unknown. Row order,
    index and every other column are unchanged."""
    if "position_source" not in frame or frame.empty:
        return frame
    cols, joins = _snapshot_join("AND snapshot_at <= ?")
    keys = frame[["season", "gw", "fpl_id"]].reset_index(drop=True)
    keys.insert(0, "_row", np.arange(len(frame)))
    name = f"_snapshot_keys_{id(keys)}"
    db.con.register(name, keys)
    try:
        got = db.query(f"SELECT f._row, {cols} FROM {name} f {joins} ORDER BY f._row",
                       [as_of, as_of])  # fmt: skip
    finally:
        db.con.unregister(name)
    out = frame.copy()
    for col in _SNAPSHOT_FIELDS:
        out[col] = got[col].to_numpy()
    return out


def player_gw_frame(db: FootballDB, min_season: int | None = None) -> pd.DataFrame:
    """One row per player per fixture: minutes, event counts, official points, price, status.
    Blank-gameweek placeholders (fixture 0) are not matches and are left out.

    Sorted chronologically per player. Squad snapshots supply team, position and price; for the
    current season several snapshots exist per gameweek and the last one is used.

    A gameweek without its own snapshot keeps team, price, status and chance unknown (NULL):
    those change during a season and a later snapshot is not evidence of the earlier value.
    Only position is filled, because an FPL position is fixed for the whole season, and only
    when every snapshot of that season agrees; `position_source` says which rows were filled
    ("snapshot" = this gameweek's own, "season" = the season's agreed position, NULL = unknown);
    `snapshot_kind` says whether the gameweek's own snapshot was observed (an FPL bootstrap
    pull) or reconstructed (the vaastav archive: kickoff-time stamp, assumed status).
    The selection uses every stored snapshot; a caller with a cutoff applies `snapshot_as_of`.
    """
    fields = ", ".join(f"{_json(src)} AS {col}" for col, src in STAT_FIELDS.items())
    cols, joins = _snapshot_join("")
    sql = f"""
        SELECT f.player_id, f.fpl_id, f.season, f.gw, f.fixture_id, f.kickoff,
               f.ingested_at AS stats_ingested_at, f.minutes,
               f.total_points AS points, f.bps, f.bonus, {fields},
               {cols}
        FROM fpl_points_gw f{joins}
        WHERE f.player_id IS NOT NULL
          AND f.fixture_id <> 0 AND f.minutes IS NOT NULL  -- blank-GW placeholder, unknown split
    """
    params: list = []
    if min_season is not None:
        sql += " AND f.season >= ?"
        params.append(min_season)
    df = db.query(sql, params)
    if df.empty:
        return df
    df["started"] = np.where(df["starts"].notna(), df["starts"] > 0, df["minutes"] >= 60)
    df["started"] = df["started"].astype(bool)
    # a double gameweek has two rows per player: collapse nothing, keep fixture order
    df = df.sort_values(["player_id", "season", "gw", "kickoff", "fixture_id"], kind="stable")
    return df.reset_index(drop=True)


def _prev_rolling(s: pd.Series, by: pd.Series, window: int) -> pd.Series:
    return s.groupby(by).transform(lambda x: x.shift(1).rolling(window, min_periods=1).mean())


def minutes_features(df: pd.DataFrame) -> pd.DataFrame:
    """Add form features for predicting a player's minutes in each row from earlier rows."""
    out = df.copy()
    g = out["player_id"]
    played = (out["minutes"] > 0).astype(float)
    started = out["started"].astype(float)
    for w in (3, 5, 10):
        out[f"m{w}"] = _prev_rolling(out["minutes"].astype(float), g, w)
        out[f"s{w}"] = _prev_rolling(started, g, w)
    out["p5"] = _prev_rolling(played, g, 5)
    # long-run view: everything before this row, exponentially weighted (~30 fixtures)
    out["s_all"] = started.groupby(g).transform(lambda x: x.shift(1).ewm(halflife=30).mean())
    out["m_all"] = (
        out["minutes"]
        .astype(float)
        .groupby(g)
        .transform(lambda x: x.shift(1).ewm(halflife=30).mean())
    )
    out["last_minutes"] = out.groupby("player_id")["minutes"].shift(1)
    out["last_started"] = started.groupby(g).shift(1)
    out["n_prev"] = out.groupby("player_id").cumcount().clip(upper=30)  # "how new", not "how old"
    out["n_prev_season"] = out.groupby(["player_id", "season"]).cumcount()
    prev_season = out.groupby("player_id")["season"].shift(1)
    out["first_of_season"] = (prev_season != out["season"]).astype(float)
    out["days_rest"] = (out["kickoff"] - out.groupby("player_id")["kickoff"].shift(1)).dt.days
    # consecutive zero-minute fixtures before this one (injury / dropped / not registered)
    zero = (out["minutes"] == 0).astype(int)
    prior_zero = zero.groupby(g).shift(1).fillna(0)
    run = prior_zero.groupby([g, (prior_zero == 0).groupby(g).cumsum()]).cumsum()
    out["zero_streak"] = run
    out["pos_code"] = out["position"].map(POSITION_CODE)
    team_pos_max = out.groupby(["season", "gw", "team_id", "position"])["price"].transform("max")
    out["price_vs_best"] = out["price"] - team_pos_max
    out["gw_number"] = out["gw"]
    return out


MINUTES_FEATURES = [
    "m3", "m5", "m10", "s3", "s5", "s10", "p5", "s_all", "m_all",
    "last_minutes", "last_started", "n_prev",
    "n_prev_season", "first_of_season", "days_rest", "zero_streak", "pos_code", "price",
    "price_vs_best", "gw_number",
]  # fmt: skip


MATCH_STATS = ("xG", "xA", "cbit", "cbirt", "goals", "assists", "yellow_cards", "red_cards")


def match_frame(db: FootballDB, source: str = "fotmob") -> pd.DataFrame:
    """One row per player per match from one detailed source (FotMob): minutes, per-match xG, xA
    and defensive-action counts, plus the team's own xG in that match (the exposure a player's
    attacking output is measured against). Where applied evidence links exist
    (`db.player_links`), each row also carries `link_to`, `link_valid_from` and
    `link_recorded_at`: the FPL player its record is linked to, from when the evidence supported
    it and when the link was written; `player_links.resolve` uses them at a cutoff, and the row's
    own `player_id` is left as stored."""
    stats = ", ".join(
        f"max(CASE WHEN s.stat = '{k}' THEN s.value END) AS {k.lower()}" for k in MATCH_STATS
    )
    sql = f"""
        SELECT a.match_id, a.player_id, a.team_id, m.competition_id AS comp, m.season,
               m.match_date AS date, a.minutes, a.position, tm.xg_for AS team_xg, {stats}
        FROM appearances a
        JOIN matches m USING (match_id)
        LEFT JOIN player_match_stats s ON s.match_id = a.match_id AND s.player_id = a.player_id
              AND s.source = a.source
        LEFT JOIN team_match tm ON tm.match_id = a.match_id AND tm.team_id = a.team_id
              AND tm.source = a.source
        WHERE a.source = ? AND a.minutes IS NOT NULL
        GROUP BY ALL
    """
    if "player_links" not in getattr(db, "missing_tables", []):
        sql = f"""
            SELECT f.*, l.to_player_id AS link_to, l.valid_from AS link_valid_from,
                   l.recorded_at AS link_recorded_at
            FROM ({sql}) f LEFT JOIN player_links l ON l.from_player_id = f.player_id
                 AND l.source = ? AND l.rolled_back_at IS NULL
        """
        df = db.query(sql, [source, source])
    else:
        df = db.query(sql, [source])
    if not df.empty:
        df["date"] = pd.to_datetime(df["date"])
    return df
