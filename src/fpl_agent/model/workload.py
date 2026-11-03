"""Rest and recent workload across every competition we hold (league, Europe, other leagues).

Features for a player before a fixture kicking off at `t`, from appearances whose match facts
were public by the information cutoff (kickoff + 3 hours <= cutoff; for a training row the
cutoff is its own kickoff, so only earlier matches count):

| feature | unit | missing (NaN) when |
|---|---|---|
| `xc_rest_days` | days since the player's last appearance with minutes, any competition | no appearance with minutes in the 60 days before |
| `xc_mins_7d` / `xc_mins_14d` | minutes played in the 7 / 14 days before | the player has no appearance rows at all in the 365 days before (no coverage), never 0 by default |
| `xc_team_matches_7d` | finished matches the player's club played in the 7 days before | the club has no match rows at all in the 60 days before |

Unknown stays NaN (the gradient-boosted model handles it natively; at inference a player with
no workload coverage keeps the baseline forecast). Sources: `appearances` (FotMob lineups and
minutes for 15 competitions) and `matches`. Domestic cups are not in the database, so a cup
midweek is invisible: a documented limitation.
"""

from __future__ import annotations

from datetime import datetime

import numpy as np
import pandas as pd

from fpl_agent.db.database import FootballDB
from fpl_agent.pit import MATCH_FACT_DELAY

FEATURES = ["xc_rest_days", "xc_mins_7d", "xc_mins_14d", "xc_team_matches_7d"]
REST_WINDOW, COVER_WINDOW, TEAM_WINDOW = 60, 365, 60  # days


def appearance_log(db: FootballDB) -> pd.DataFrame:
    """One row per (player, match) across competitions: kickoff and minutes."""
    df = db.query(
        "SELECT a.player_id, a.match_id, coalesce(m.kickoff, CAST(m.match_date AS TIMESTAMP) "
        "+ INTERVAL 15 HOUR) AS kickoff, max(a.minutes) AS minutes FROM appearances a "
        "JOIN matches m USING (match_id) WHERE a.minutes IS NOT NULL AND a.player_id IS NOT NULL "
        "GROUP BY ALL"
    )
    if not df.empty:
        df["kickoff"] = pd.to_datetime(df["kickoff"])
    return df.sort_values(["player_id", "kickoff"]).reset_index(drop=True)


def team_log(db: FootballDB) -> pd.DataFrame:
    df = db.query(
        "SELECT team_id, kickoff FROM (SELECT home_team_id AS team_id, coalesce(kickoff, "
        "CAST(match_date AS TIMESTAMP) + INTERVAL 15 HOUR) AS kickoff FROM matches WHERE finished "
        "UNION ALL SELECT away_team_id, coalesce(kickoff, CAST(match_date AS TIMESTAMP) "
        "+ INTERVAL 15 HOUR) FROM matches WHERE finished) WHERE team_id IS NOT NULL"
    )
    if not df.empty:
        df["kickoff"] = pd.to_datetime(df["kickoff"])
    return df.sort_values(["team_id", "kickoff"]).reset_index(drop=True)


def _window_sum(times: np.ndarray, vals: np.ndarray, start: np.ndarray, end: np.ndarray):
    """sum(vals[i]) for times[i] in [start, end) per query, via prefix sums."""
    csum = np.concatenate([[0.0], np.cumsum(vals)])
    return csum[np.searchsorted(times, end, "left")] - csum[np.searchsorted(times, start, "left")]


def workload_features(
    targets: pd.DataFrame,
    apps: pd.DataFrame,
    teams: pd.DataFrame,
    cutoff: datetime | None = None,
) -> pd.DataFrame:
    """`targets` has player_id, team_id, kickoff (one row per fixture to describe). Returns
    FEATURES aligned with `targets`' index. Facts known by min(kickoff, cutoff) - 3h are used."""
    out = pd.DataFrame(np.nan, index=targets.index, columns=FEATURES)
    if targets.empty:
        return out
    ko = pd.to_datetime(targets["kickoff"]).to_numpy("datetime64[ns]")
    known = ko.copy()
    if cutoff is not None:
        known = np.minimum(known, np.datetime64(pd.Timestamp(cutoff), "ns"))
    known = known - np.timedelta64(int(MATCH_FACT_DELAY.total_seconds()), "s")
    day = np.timedelta64(1, "D")
    by_player = {p: g for p, g in apps.groupby("player_id")} if not apps.empty else {}
    by_team = {t: g for t, g in teams.groupby("team_id")} if not teams.empty else {}
    pids = targets["player_id"].to_numpy()
    tids = targets["team_id"].to_numpy() if "team_id" in targets else np.full(len(targets), None)
    for pid in np.unique(pids):
        rows = np.flatnonzero(pids == pid)
        g = by_player.get(pid)
        if g is None:
            continue
        t = g["kickoff"].to_numpy("datetime64[ns]")
        m = g["minutes"].to_numpy(float)
        end = known[rows]
        cover = _window_sum(t, np.ones_like(m), end - COVER_WINDOW * day, end) > 0
        for w, col in ((7, "xc_mins_7d"), (14, "xc_mins_14d")):
            # the window ends at kickoff, but only facts known by `end` exist
            s = _window_sum(t, m, ko[rows] - w * day, end)
            out.iloc[rows, out.columns.get_loc(col)] = np.where(cover, s, np.nan)
        played = t[m > 0]
        idx = np.searchsorted(played, end, "left") - 1
        last = np.where(idx >= 0, played[np.clip(idx, 0, None)] if len(played) else ko[rows],
                        np.datetime64("NaT", "ns"))  # fmt: skip
        rest = (ko[rows] - last) / day
        ok = (idx >= 0) & (rest <= REST_WINDOW)
        out.iloc[rows, out.columns.get_loc("xc_rest_days")] = np.where(ok, rest, np.nan)
    for tid in pd.unique(tids):
        if tid is None or (isinstance(tid, float) and np.isnan(tid)):
            continue
        rows = np.flatnonzero(tids == tid)
        g = by_team.get(tid)
        if g is None:
            continue
        t = g["kickoff"].to_numpy("datetime64[ns]")
        end = known[rows]
        n7 = _window_sum(t, np.ones(len(t)), ko[rows] - 7 * day, end)
        cov = _window_sum(t, np.ones(len(t)), end - TEAM_WINDOW * day, end) > 0
        out.iloc[rows, out.columns.get_loc("xc_team_matches_7d")] = np.where(cov, n7, np.nan)
    return out
