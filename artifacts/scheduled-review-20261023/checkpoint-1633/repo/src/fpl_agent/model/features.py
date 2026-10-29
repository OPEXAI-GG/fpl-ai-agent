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


def season_positions(db: FootballDB, as_of: datetime | None = None) -> dict[tuple[int, int], str]:
    """{(season, player_id): position} where every snapshot of that season taken at or before
    `as_of` (all of them when None) agrees. Point in time: a later snapshot cannot add, change
    or remove an earlier cutoff's answer."""
    sql = _SEASON_POSITION.format(key="player_id",
                                  cutoff="AND snapshot_at <= ?" if as_of else "")  # fmt: skip
    rows = db.con.execute(sql, [as_of] if as_of else []).fetchall()
    return {(s, p): pos for s, p, pos in rows if p is not None}


def positions_as_of(frame: pd.DataFrame, db: FootballDB, as_of: datetime) -> pd.DataFrame:
    """`frame` with its season-filled positions (`position_source == "season"`) re-derived from
    snapshots taken at or before `as_of`, so one frame built from the whole database can serve
    any cutoff (replay, backtest) without a later observation leaking into an earlier one."""
    if "position_source" not in frame or not (frame["position_source"] == "season").any():
        return frame
    known = season_positions(db, as_of)
    out = frame.copy()
    rows = out["position_source"] == "season"
    pos = [known.get((s, p)) for s, p in zip(out.loc[rows, "season"], out.loc[rows, "player_id"],
                                                strict=True)]  # fmt: skip
    out.loc[rows, "position"] = pos
    out.loc[rows, "position_source"] = ["season" if p is not None else None for p in pos]
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
    ("snapshot" = this gameweek's own, "season" = the season's agreed position, NULL = unknown).
    The season fill uses every stored snapshot; a caller with a cutoff applies `positions_as_of`.
    """
    fields = ", ".join(f"{_json(src)} AS {col}" for col, src in STAT_FIELDS.items())
    sql = f"""
        SELECT f.player_id, f.season, f.gw, f.fixture_id, f.kickoff, f.minutes,
               f.total_points AS points, f.bps, f.bonus, {fields},
               s.team_id, coalesce(s.position, sp.position) AS position,
               s.now_cost AS price, s.status, s.chance_next AS chance,
               CASE WHEN s.position IS NOT NULL THEN 'snapshot'
                    WHEN sp.position IS NOT NULL THEN 'season' END AS position_source
        FROM fpl_points_gw f
        LEFT JOIN (
            SELECT * FROM fpl_players_gw
            QUALIFY snapshot_at = max(snapshot_at) OVER (PARTITION BY season, gw, fpl_id)
        ) s ON s.season = f.season AND s.gw = f.gw AND s.fpl_id = f.fpl_id
        LEFT JOIN ({_SEASON_POSITION.format(key="fpl_id", cutoff="")}
        ) sp ON sp.season = f.season AND sp.fpl_id = f.fpl_id
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
    attacking output is measured against)."""
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
    df = db.query(sql, [source])
    if not df.empty:
        df["date"] = pd.to_datetime(df["date"])
    return df
