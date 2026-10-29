"""U07 experiment (acceptance set BEFORE running): a vectorised minutes_features must give an
IDENTICAL frame (pandas.testing.assert_frame_equal, check_exact=True) on the work snapshot and be
>= 2x faster (median of 3 warm runs). Otherwise the baseline stays.

usage: python perf_minutes_experiment.py DBCOPY OUT.json
"""
import json, statistics, sys, time
import pandas as pd
from fpl_agent.db.database import FootballDB
from fpl_agent.model import features as F


def fast(df):
    out = df.copy()
    g = out["player_id"]
    played = (out["minutes"] > 0).astype(float)
    started = out["started"].astype(float)
    mins = out["minutes"].astype(float)

    def prev_roll(s, w):
        return s.groupby(g).shift(1).groupby(g).rolling(w, min_periods=1).mean().reset_index(level=0, drop=True)

    def prev_ewm(s):
        return s.groupby(g).shift(1).groupby(g).ewm(halflife=30).mean().reset_index(level=0, drop=True)

    for w in (3, 5, 10):
        out[f"m{w}"] = prev_roll(mins, w)
        out[f"s{w}"] = prev_roll(started, w)
    out["p5"] = prev_roll(played, 5)
    out["s_all"] = prev_ewm(started)
    out["m_all"] = prev_ewm(mins)
    rest = F.minutes_features(df.iloc[:0])  # column order reference
    base = F.minutes_features  # remaining columns: reuse the original code path exactly
    return out, base


db = FootballDB(sys.argv[1])
frame = F.player_gw_frame(db)
ref = F.minutes_features(frame)
cols = ["m3", "s3", "m5", "s5", "m10", "s10", "p5", "s_all", "m_all"]
got, _ = fast(frame)
res = {"rows": len(frame)}
try:
    pd.testing.assert_frame_equal(ref[cols], got[cols], check_exact=True)
    res["identical"] = True
except AssertionError as e:
    res["identical"] = False
    res["difference"] = str(e)[:500]
    diff = (ref[cols] - got[cols]).abs().max().max()
    res["max_abs_diff"] = float(diff)


def timed(fn):
    t = []
    for _ in range(4):
        s = time.perf_counter(); fn(); t.append(time.perf_counter() - s)
    return round(statistics.median(t[1:]), 3)


res["baseline_rolling_cols_s"] = timed(lambda: [F._prev_rolling(frame["minutes"].astype(float), frame["player_id"], w) for w in (3, 5, 10)])
res["fast_rolling_cols_s"] = timed(lambda: fast(frame))
res["baseline_full_s"] = timed(lambda: F.minutes_features(frame))
db.close()
print(json.dumps(res, indent=1))
open(sys.argv[2], "w").write(json.dumps(res, indent=1))
