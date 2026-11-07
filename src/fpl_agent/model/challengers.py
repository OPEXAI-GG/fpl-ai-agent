"""Shadow forecast challengers, evaluated on chronological splits. Nothing here changes an issued
forecast, a plan or the learning state: results are written as labelled evaluation files.

Three challengers, all fitted on point-in-time features (every feature of a player-gameweek is
built from earlier gameweeks only):

* **direct points**: a gradient-boosted regressor that predicts a player's FPL points for the
  gameweek straight from his history, against the simulator (which builds points from minutes,
  team strength and contributions);
* **minutes**: a classifier for four exclusive outcomes (no appearance, cameo, start without
  reaching 60, 60+), giving P(start), P(60+), P(cameo) and P(no appearance). "Available" in FPL is
  not a start: the classifier is scored on what happened;
* **contribution** features inside both: shrunk per-90 expected goals and assists (empirical-Bayes
  towards the position average, so a few lucky minutes do not dominate), opponent-adjusted
  attack/defence form and European workload. Each block is ablated on matched rows.

Evaluation units are gameweeks: players in one gameweek share fixtures and news, so uncertainty
is a standard error over per-gameweek mean differences, never over player rows.

Known gaps (documented, not imputed): FPL's `expected_goals` includes penalties (no npxG in the
public data); xG/xA, starts and FPL's own xP exist only from 2022/23; no home/away flag per
player-gameweek is stored for older seasons; no news or social signals are used (by design).
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import Any

import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier, HistGradientBoostingRegressor

from fpl_agent.db.database import FootballDB
from fpl_agent.learning import THRESHOLDS

EURO = ("UCL", "UEL", "UECL")
POS = {"GKP": 0, "DEF": 1, "MID": 2, "FWD": 3}
SHRINK_K = 3.0  # prior strength in 90-minute matches for the per-90 contribution rates
CLASSES = ("none", "cameo", "start_short", "full")  # 0 min / sub / started <60 / 60+

FORM = [
    "pts_r3",
    "pts_r5",
    "pts_r10",
    "min_r3",
    "min_r5",
    "app_r5",
    "full_r5",
    "bonus_r5",
    "bps_r5",
    "start_r5",
    "weeks_seen",
]
# ownership is not in the older snapshots, and status/chance only in this season's: availability
# flags are constant in the training seasons, so the challenger cannot learn from them (a gap in
# the simulator's favour, which reads them directly)
STATE = ["price", "chance", "unavailable", "pos", "fixtures"]
CONTRIB = ["xg90", "xa90", "xgi90", "xgc_r5"]
OPP = ["opp_xga_r6", "opp_xgf_r6", "opp_conc_pos_r6"]
WORK = ["euro_min_7d", "team_euro_7d"]
ARMS = {
    "all features": FORM + STATE + CONTRIB + OPP + WORK,
    "without contribution (xG/xA)": FORM + STATE + OPP + WORK,
    "without opponent adjustment": FORM + STATE + CONTRIB + WORK,
    "without European workload": FORM + STATE + CONTRIB + OPP,
    "form and state only": FORM + STATE,
}


# ---- point-in-time feature table ---------------------------------------------------------------
def _stat(js: str | None, key: str) -> float | None:
    if not js:
        return None
    try:
        v = json.loads(js).get(key)
    except (TypeError, ValueError):
        return None
    try:
        return None if v is None else float(v)
    except (TypeError, ValueError):
        return None


def player_weeks(db: FootballDB, seasons: list[int]) -> pd.DataFrame:
    """One row per player and gameweek with a fixture: outcome, pre-gameweek state and every
    feature, all features from strictly earlier gameweeks."""
    marks = ",".join("?" for _ in seasons)
    raw = db.query(
        f"SELECT season, gw, player_id, fixture_id, minutes, total_points, bonus, bps, stats, "
        f"kickoff FROM fpl_points_gw WHERE season IN ({marks}) AND player_id IS NOT NULL",
        list(seasons),
    )
    for k in ("starts", "expected_goals", "expected_assists", "expected_goals_conceded", "xP"):
        raw[k] = [_stat(s, k) for s in raw["stats"]]
    raw = raw.drop(columns=["stats"])
    snap = db.query(
        f"SELECT p.season, p.gw, p.player_id, p.team_id, p.position, p.now_cost, "
        f"p.selected_by_percent, p.status, p.chance_next FROM fpl_players_gw p LEFT JOIN "
        f"fpl_events e ON e.season = p.season AND e.gw = p.gw WHERE p.season IN ({marks}) "
        "AND (e.deadline_time IS NULL OR p.snapshot_at <= e.deadline_time) "  # pre-deadline only
        "QUALIFY p.snapshot_at = max(p.snapshot_at) OVER (PARTITION BY p.season, p.gw, "
        "p.player_id)",
        list(seasons),
    ).drop_duplicates(["season", "gw", "player_id"])
    raw = raw.merge(
        snap[["season", "gw", "player_id", "team_id", "position"]],
        on=["season", "gw", "player_id"],
        how="left",
    )
    raw = raw.dropna(subset=["team_id", "position"])
    team_fix = _team_fixtures(raw)
    g = raw.groupby(["season", "gw", "player_id"], as_index=False).agg(
        minutes=("minutes", "sum"),
        points=("total_points", "sum"),
        bonus=("bonus", "sum"),
        bps=("bps", "sum"),
        starts=("starts", "sum"),
        starts_known=("starts", lambda s: s.notna().all()),
        xg=("expected_goals", "sum"),
        xa=("expected_assists", "sum"),
        xgc=("expected_goals_conceded", "sum"),
        x_known=("expected_goals", lambda s: s.notna().all()),
        fpl_xp=("xP", lambda s: s.sum() if s.notna().all() else np.nan),
        fixtures=("fixture_id", "nunique"),
        kickoff=("kickoff", "min"),
        team_id=("team_id", "first"),
        position=("position", "first"),
    )
    g.loc[~g["x_known"], ["xg", "xa", "xgc"]] = np.nan
    g.loc[~g["starts_known"], "starts"] = np.nan
    g = g.merge(
        snap.drop(columns=["team_id", "position"]), on=["season", "gw", "player_id"], how="left"
    )
    g["price"] = g["now_cost"].astype(float)
    g["sel"] = pd.to_numeric(g["selected_by_percent"], errors="coerce")
    g["chance"] = g["chance_next"].astype(float).fillna(100.0)
    g["unavailable"] = (~g["status"].fillna("a").isin(["a"])).astype(float)
    g["pos"] = g["position"].map(POS).astype(float)
    g = g.sort_values(["player_id", "season", "gw"]).reset_index(drop=True)
    _rolling_form(g)
    _contribution(g)
    g = _opponent(g, team_fix)
    g = _workload(db, g)
    g["outcome"] = _outcome(g)
    return g


def _team_fixtures(raw: pd.DataFrame) -> pd.DataFrame:
    """Per team and fixture: xG for and against and FPL points conceded to each position."""
    t = raw.groupby(["season", "gw", "fixture_id", "team_id"], as_index=False).agg(
        xgf=("expected_goals", lambda s: s.sum() if s.notna().any() else np.nan),
        kickoff=("kickoff", "min"),
    )
    pts = raw.pivot_table(
        index=["season", "fixture_id", "team_id"],
        columns="position",
        values="total_points",
        aggfunc="sum",
        fill_value=0,
    ).reset_index()
    t = t.merge(pts, on=["season", "fixture_id", "team_id"], how="left")
    opp = t[["season", "fixture_id", "team_id", "xgf", *[c for c in POS if c in t]]].rename(
        columns={"team_id": "opp_id", "xgf": "xga", **{p: f"conc_{p}" for p in POS}}
    )
    t = t.merge(opp, on=["season", "fixture_id"])
    t = t[t["team_id"] != t["opp_id"]].sort_values(["team_id", "kickoff"]).reset_index(drop=True)
    by = t.groupby("team_id")
    t["opp_xgf_r6"] = by["xgf"].transform(lambda s: s.shift(1).rolling(6, min_periods=2).mean())
    t["opp_xga_r6"] = by["xga"].transform(lambda s: s.shift(1).rolling(6, min_periods=2).mean())
    for p in POS:
        col = f"conc_{p}"
        if col in t:
            t[f"conc_{p}_r6"] = by[col].transform(
                lambda s: s.shift(1).rolling(6, min_periods=2).mean()
            )
    return t


def _rolling_form(g: pd.DataFrame) -> None:
    by = g.groupby("player_id")
    roll = lambda col, n, fn="mean": by[col].transform(  # noqa: E731
        lambda s: getattr(s.shift(1).rolling(n, min_periods=1), fn)()
    )
    g["full"] = (g["minutes"] >= 60 * g["fixtures"].clip(lower=1)).astype(float)
    g["app"] = (g["minutes"] > 0).astype(float)
    for n in (3, 5, 10):
        g[f"pts_r{n}"] = roll("points", n)
    g["min_r3"], g["min_r5"] = roll("minutes", 3), roll("minutes", 5)
    g["app_r5"], g["full_r5"] = roll("app", 5), roll("full", 5)
    g["bonus_r5"], g["bps_r5"] = roll("bonus", 5), roll("bps", 5)
    g["start_r5"] = roll("starts", 5)
    g["weeks_seen"] = by.cumcount().astype(float)


def _contribution(g: pd.DataFrame) -> None:
    """Shrunk per-90 xG, xA and xG involvement over the last ten player-gameweeks; position
    priors are the pooled rates of that position in earlier gameweeks only."""
    by = g.groupby("player_id")
    s = lambda col: by[col].transform(  # noqa: E731
        lambda x: x.shift(1).rolling(10, min_periods=1).sum()
    )
    m90 = (
        by["minutes"].transform(
            lambda x: x.where(g.loc[x.index, "x_known"]).shift(1).rolling(10, min_periods=1).sum()
        )
        / 90.0
    )
    known = g[g["x_known"]]
    for col, out in (("xg", "xg90"), ("xa", "xa90")):
        tot = s(col)
        # the position's pooled rate over strictly earlier gameweeks (expanding, point in time)
        wk = known.groupby(["position", "season", "gw"])[[col, "minutes"]].sum().sort_index()
        cum = wk.groupby(level="position").cumsum().groupby(level="position").shift(1)
        rate = (cum[col] / (cum["minutes"] / 90)).rename("_prior").reset_index()
        pr = g[["position", "season", "gw"]].merge(rate, how="left")["_prior"]
        pr = pr.fillna(0.0).to_numpy()
        g[out] = (tot.fillna(0) + SHRINK_K * pr) / (m90.fillna(0) + SHRINK_K)
        g.loc[tot.isna(), out] = np.nan
    g["xgi90"] = g["xg90"] + g["xa90"]
    g["xgc_r5"] = by["xgc"].transform(lambda x: x.shift(1).rolling(5, min_periods=1).mean())


def _opponent(g: pd.DataFrame, t: pd.DataFrame) -> pd.DataFrame:
    """The opponent's form before the fixture (mean over a double gameweek's fixtures)."""
    # the opponent's rolling values at the fixture itself: keep the row for that fixture
    fx = t[["season", "gw", "team_id", "fixture_id", "opp_id"]]
    opp = fx.merge(
        t[
            [
                "season",
                "fixture_id",
                "team_id",
                "opp_xgf_r6",
                "opp_xga_r6",
                *[f"conc_{p}_r6" for p in POS if f"conc_{p}_r6" in t],
            ]
        ].rename(columns={"team_id": "opp_id"}),
        on=["season", "fixture_id", "opp_id"],
        how="left",
    )
    agg = opp.groupby(["season", "gw", "team_id"], as_index=False).mean(numeric_only=True)
    g = g.merge(
        agg.drop(columns=["fixture_id", "opp_id"], errors="ignore"),
        on=["season", "gw", "team_id"],
        how="left",
    )
    # attack of the opponent hurts defenders and keepers; its defence helps attackers
    conc = np.full(len(g), np.nan)
    for p in POS:
        c = f"conc_{p}_r6"
        if c in g:
            m = (g["position"] == p).to_numpy()
            conc[m] = g.loc[m, c].to_numpy()
    g["opp_conc_pos_r6"] = conc
    return g.drop(columns=[c for c in g.columns if c.startswith("conc_")])


def _workload(db: FootballDB, g: pd.DataFrame) -> pd.DataFrame:
    """Minutes in European club matches in the seven days before the gameweek's first kickoff,
    and whether the player's club played one. Players from clubs outside Europe score zero."""
    marks = ",".join("?" for _ in EURO)
    eu = db.query(
        f"SELECT a.player_id, a.team_id, a.minutes, m.kickoff FROM appearances a JOIN matches m "
        f"USING (match_id) WHERE m.competition_id IN ({marks}) AND m.kickoff IS NOT NULL",
        list(EURO),
    )
    g["euro_min_7d"] = 0.0
    g["team_euro_7d"] = 0.0
    if eu.empty or g["kickoff"].isna().all():
        return g
    k = pd.to_datetime(g["kickoff"]).dt.tz_localize(None)
    eu["kickoff"] = pd.to_datetime(eu["kickoff"]).dt.tz_localize(None)
    lo = k - pd.Timedelta(days=7)
    p = (
        g[["player_id"]]
        .assign(i=np.arange(len(g)), lo=lo, hi=k)
        .merge(eu[["player_id", "minutes", "kickoff"]], on="player_id")
    )
    p = p[(p["kickoff"] >= p["lo"]) & (p["kickoff"] < p["hi"])]
    g.loc[:, "euro_min_7d"] = (
        p.groupby("i")["minutes"].sum().reindex(np.arange(len(g))).fillna(0).to_numpy()
    )
    teams = eu.drop_duplicates(["team_id", "kickoff"])[["team_id", "kickoff"]]
    t = g[["team_id"]].assign(i=np.arange(len(g)), lo=lo, hi=k).merge(teams, on="team_id")
    t = t[(t["kickoff"] >= t["lo"]) & (t["kickoff"] < t["hi"])]
    g.loc[:, "team_euro_7d"] = (
        t.groupby("i").size().reindex(np.arange(len(g))).fillna(0).clip(upper=1).to_numpy()
    )
    return g


def _outcome(g: pd.DataFrame) -> pd.Series:
    """The exclusive minutes outcome of a single-fixture gameweek (NaN otherwise or without a
    recorded start)."""
    out = pd.Series(np.nan, index=g.index)
    one = (g["fixtures"] == 1) & g["starts"].notna()
    m, s = g["minutes"], g["starts"]
    out[one & (m == 0)] = 0
    out[one & (m > 0) & (s == 0)] = 1
    out[one & (s > 0) & (m < 60)] = 2
    out[one & (s > 0) & (m >= 60)] = 3
    return out


# ---- models --------------------------------------------------------------------------------
def fit_points(train: pd.DataFrame, cols: list[str], seed: int = 0):
    m = HistGradientBoostingRegressor(
        loss="squared_error",  # an expected value, like xP (not a median)
        max_iter=300,
        learning_rate=0.05,
        max_leaf_nodes=31,
        min_samples_leaf=80,
        l2_regularization=1.0,
        early_stopping=False,  # fixed rounds: the same data always gives the same model
        random_state=seed,
    )
    m.fit(train[cols].to_numpy(float), train["points"].to_numpy(float))
    return m


def fit_minutes(train: pd.DataFrame, cols: list[str], seed: int = 0):
    t = train.dropna(subset=["outcome"])
    m = HistGradientBoostingClassifier(
        max_iter=250,
        learning_rate=0.05,
        max_leaf_nodes=31,
        min_samples_leaf=80,
        l2_regularization=1.0,
        early_stopping=False,  # fixed rounds: the same data always gives the same model
        random_state=seed,
    )
    m.fit(t[cols].to_numpy(float), t["outcome"].astype(int).to_numpy())
    return m


def class_probs(m, df: pd.DataFrame, cols: list[str]) -> np.ndarray:
    p = np.zeros((len(df), 4))
    p[:, m.classes_.astype(int)] = m.predict_proba(df[cols].to_numpy(float))
    return p


def baseline_probs(df: pd.DataFrame) -> np.ndarray:
    """Last-five frequencies of the outcomes, Laplace-smoothed: the naive minutes baseline."""
    app, full, st = df["app_r5"].fillna(0.5), df["full_r5"].fillna(0.3), df["start_r5"]
    st = st.fillna(full)
    n = np.minimum(df["weeks_seen"].fillna(0), 5)
    sm = lambda x, k: (x * n + 1 / 4 * k) / (n + k)  # noqa: E731
    p_none = sm(1 - app, 1.0)
    p_full = sm(full, 1.0)
    p_start = sm(st.clip(lower=full), 1.0).clip(lower=p_full)
    p_cameo = (1 - p_none - p_start).clip(lower=0.0)
    p = np.stack([p_none, p_cameo, p_start - p_full, p_full], axis=1).astype(float)
    p = np.clip(p, 1e-4, None)
    return p / p.sum(axis=1, keepdims=True)


# ---- evaluation ------------------------------------------------------------------------------
@dataclass
class Block:
    """Paired per-gameweek comparison of arm `a` against arm `b` (negative: `a` is better)."""

    mean: float | None
    se: float | None
    weeks: int
    per_week: list[float]

    def row(self) -> dict[str, Any]:
        return {
            "mean_difference": _r(self.mean),
            "se": _r(self.se),
            "gameweeks": self.weeks,
            "per_week": [_r(x, 3) for x in self.per_week],
        }


def block(df: pd.DataFrame, a: str, b: str, loss=lambda p, y: np.abs(p - y)) -> Block:
    d = df.assign(_d=loss(df[a], df["y"]) - loss(df[b], df["y"]))
    wk = d.groupby(["season", "gw"])["_d"].mean().to_numpy()
    n = len(wk)
    return Block(
        float(wk.mean()) if n else None,
        float(wk.std(ddof=1) / np.sqrt(n)) if n > 1 else None,
        n,
        wk.tolist(),
    )


def verdict(b: Block, what: str) -> str:
    k, need = THRESHOLDS["se_multiple"], THRESHOLDS["min_gameweeks"]
    if b.weeks < need or b.se is None:
        return f"insufficient evidence: {b.weeks} of {need} gameweeks"
    if b.mean + k * b.se < 0:
        return f"{what} lower by more than {k:g} SE across gameweeks"
    if b.mean - k * b.se > 0:
        return f"{what} higher by more than {k:g} SE across gameweeks"
    return "no difference established"


def _r(x, n: int = 4):
    return None if x is None or (isinstance(x, float) and np.isnan(x)) else round(float(x), n)


def _mae(df, col):
    return _r(float((df[col] - df["y"]).abs().mean()))


def _rmse(df, col):
    return _r(float(np.sqrt(((df[col] - df["y"]) ** 2).mean())))


def _season(y: int) -> str:
    return f"{y}/{str(y + 1)[2:]}"


def points_backtest(pw: pd.DataFrame, train: list[int], tune: int, test: int) -> dict[str, Any]:
    """Direct-points challenger against FPL's recorded xP and the last-5 average on a held-out
    season, with each feature block ablated on the same rows. The tuning season picks the
    number of boosting rounds; the test season is used once."""
    t0 = time.time()
    tr = pw[pw["season"].isin(train)]
    tu = pw[pw["season"] == tune]
    te = pw[(pw["season"] == test) & pw["fpl_xp"].notna() & pw["pts_r5"].notna()].copy()
    te["y"] = te["points"]
    te["form5"] = te["pts_r5"]
    rows, arms = [], {}
    for name, cols in ARMS.items():
        m = fit_points(pd.concat([tr, tu]), cols)
        te[name] = m.predict(te[cols].to_numpy(float))
        arms[name] = name
    full = "all features"
    for arm, col in (
        ("challenger (all features)", full),
        ("FPL xP (recorded)", "fpl_xp"),
        ("last-5 average", "form5"),
    ):
        rows.append(
            {"arm": arm, "mae": _mae(te, col), "rmse": _rmse(te, col), "rows": int(len(te))}
        )
    ablations = {}
    for name in ARMS:
        if name == full:
            continue
        b = block(te, full, name)
        b2 = block(te, full, name, lambda p, y: (p - y) ** 2)
        ablations[name] = {
            **b.row(),
            "verdict": verdict(b, "all-features absolute error"),
            "squared": {**b2.row(), "verdict": verdict(b2, "all-features squared error")},
        }
        rows.append(
            {
                "arm": f"ablation: {name}",
                "mae": _mae(te, name),
                "rmse": _rmse(te, name),
                "rows": int(len(te)),
            }
        )
    vs_xp = block(te, full, "fpl_xp")
    vs_form = block(te, full, "form5")
    sq = lambda p, y: (p - y) ** 2  # noqa: E731
    vs_xp_sq = block(te, full, "fpl_xp", sq)
    return {
        "name": "challenger-points-backtest",
        "title": "Direct points challenger: held-out season",
        "status": "shadow",
        "period": f"trained {_season(train[0])} to {_season(tune)}, tested {_season(test)}",
        "summary": (
            f"Gradient-boosted points model on point-in-time features, {len(te):,} "
            f"player-gameweeks in {vs_xp.weeks} gameweeks. Against FPL's recorded xP: "
            f"{vs_xp.mean:+.3f} MAE "
            f"(± {vs_xp.se:.3f} across gameweeks, {verdict(vs_xp, 'challenger error')}). "
            "Against "
            f"the last-5 average: {vs_form.mean:+.3f} (± {vs_form.se:.3f}). Squared error against "
            f"FPL xP: {vs_xp_sq.mean:+.3f} (± {vs_xp_sq.se:.3f})."
        ),
        "metrics": {
            "vs_fpl_xp": vs_xp.row(),
            "vs_form5": vs_form.row(),
            "vs_fpl_xp_squared": vs_xp_sq.row(),
            "ablations": ablations,
            "seconds": round(time.time() - t0, 1),
        },
        "rows": rows,
        "decision": "stays in shadow: a historical season is not forward evidence, and the "
        "simulator's own issued forecasts are not available for that season to compare against",
    }


def minutes_backtest(pw: pd.DataFrame, train: list[int], tune: int, test: int) -> dict[str, Any]:
    t0 = time.time()
    cols = ARMS["all features"]
    tr = pd.concat([pw[pw["season"].isin(train)], pw[pw["season"] == tune]])
    te = pw[(pw["season"] == test) & pw["outcome"].notna() & pw["app_r5"].notna()].copy()
    m = fit_minutes(tr, cols)
    pc, pb = class_probs(m, te, cols), baseline_probs(te)
    y = te["outcome"].astype(int).to_numpy()
    onehot = np.eye(4)[y]
    events = {
        "P(start)": (pc[:, 2] + pc[:, 3], pb[:, 2] + pb[:, 3], (y >= 2).astype(float)),
        "P(60+)": (pc[:, 3], pb[:, 3], (y == 3).astype(float)),
        "P(cameo)": (pc[:, 1], pb[:, 1], (y == 1).astype(float)),
        "P(no appearance)": (pc[:, 0], pb[:, 0], (y == 0).astype(float)),
    }
    rows, metrics = [], {}
    sq = lambda p, yy: (p - yy) ** 2  # noqa: E731
    for name, (a, b, yy) in events.items():
        d = te[["season", "gw"]].assign(a=a, b=b, y=yy)
        bl = block(d, "a", "b", sq)
        metrics[name] = {**bl.row(), "verdict": verdict(bl, "challenger Brier")}
        rows.append(
            {
                "arm": name,
                "challenger_brier": _r(float(((a - yy) ** 2).mean())),
                "last5_brier": _r(float(((b - yy) ** 2).mean())),
                "rows": int(len(te)),
            }
        )
    ll = lambda p: float(-np.log(np.clip((p * onehot).sum(axis=1), 1e-9, 1)).mean())  # noqa: E731
    metrics["log_loss"] = {"challenger": _r(ll(pc)), "last5": _r(ll(pb))}
    metrics["seconds"] = round(time.time() - t0, 1)
    st = metrics["P(start)"]
    return {
        "name": "challenger-minutes-backtest",
        "title": "Minutes challenger: start, 60+, cameo and no-appearance",
        "status": "shadow",
        "period": f"trained {_season(train[0])} to {_season(tune)}, tested {_season(test)}",
        "summary": (
            f"Four-outcome minutes classifier on {len(te):,} single-fixture player-gameweeks. "
            f"P(start) Brier difference against last-5 frequencies: {st['mean_difference']:+.4f} "
            f"± {st['se']:.4f} across gameweeks ({st['verdict']}). Log loss "
            f"{metrics['log_loss']['challenger']} against {metrics['log_loss']['last5']}."
        ),
        "metrics": metrics,
        "rows": rows,
        "decision": "stays in shadow: the simulator's minutes model is compared on issued "
        "forecasts in the forward replay, not on this season",
    }


def forward_replay(
    db: FootballDB, pw: pd.DataFrame, season: int, gws: list[int], train_seasons: list[int]
) -> dict[str, Any]:
    """The challengers against the simulator's issued forecasts on exactly the player-weeks the
    website's performance page scores. For each gameweek the challengers are refitted on
    everything before it (earlier seasons plus earlier gameweeks of this season). A blend
    weight is chosen on the first half of the gameweeks and tested on the second half."""
    from fpl_agent.web import performance

    t0 = time.time()
    cols = ARMS["all features"]
    frames = []
    for gw in gws:
        _, _, f = performance.cohort(db, season, gw)
        if f is None or f.empty:
            continue
        hist = pw[pw["season"].isin(train_seasons) | ((pw["season"] == season) & (pw["gw"] < gw))]
        cur = pw[(pw["season"] == season) & (pw["gw"] == gw)]
        mp = fit_points(hist, cols)
        mm = fit_minutes(hist, cols)
        c = cur.assign(gbm=mp.predict(cur[cols].to_numpy(float)))
        pc = class_probs(mm, c, cols)
        c = c.assign(c_start=pc[:, 2] + pc[:, 3], c_cameo=pc[:, 1])
        f = f.merge(c[["player_id", "gbm", "c_start", "c_cameo", "outcome"]], on="player_id")
        frames.append(f.assign(season=season, gw=gw))
    if not frames:
        return {
            "name": "challenger-forward-replay",
            "title": "Challengers on issued forecasts",
            "status": "unavailable",
            "summary": "No scored gameweeks to replay.",
            "rows": [],
        }
    df = pd.concat(frames, ignore_index=True)
    df["y"] = df["actual"].astype(float)
    weeks = sorted(df["gw"].unique())
    half = weeks[: max(1, len(weeks) // 2)]
    tune = df[df["gw"].isin(half)]
    grid = np.linspace(0, 1, 21)
    w = float(
        grid[
            np.argmin(
                [
                    (tune["y"] - (g * tune["xpts"] + (1 - g) * tune["gbm"])).abs().mean()
                    for g in grid
                ]
            )
        ]
    )
    df["blend"] = w * df["xpts"] + (1 - w) * df["gbm"]
    test = df[~df["gw"].isin(half)]
    vs_model = block(df, "gbm", "xpts")
    vs_ep = block(df, "gbm", "ep_next")
    blend_test = block(test, "blend", "xpts")
    one = df[df["fixtures"] == 1].assign(ys=lambda d: d["started"].astype(float))
    sq = lambda p, yy: (p - yy) ** 2  # noqa: E731
    st = Block(None, None, 0, [])
    rows = [
        {"arm": arm, "mae": _mae(d, col), "rmse": _rmse(d, col), "rows": int(len(d))}
        for arm, col, d in (
            ("simulator (issued)", "xpts", df),
            ("direct points challenger", "gbm", df),
            ("FPL expected points", "ep_next", df),
            (f"blend ({w:.2f} simulator), test weeks only", "blend", test),
            ("simulator on the same test weeks", "xpts", test),
        )
    ]
    has_start = one["p_start"].notna()
    yb = one["ys"]
    rows.append(
        {
            "arm": "P(start) Brier: minutes challenger, single-fixture rows",
            "brier": _r(float(((one["c_start"] - yb) ** 2).mean())),
            "rows": int(len(one)),
        }
    )
    if has_start.any():
        o = one[has_start]
        rows.append(
            {
                "arm": "P(start) Brier: simulator, rows where it was stored",
                "brier": _r(float(((o["p_start"] - o["ys"]) ** 2).mean())),
                "rows": int(len(o)),
            }
        )
        st = block(o.assign(y=o["ys"]), "c_start", "p_start", sq)
    return {
        "name": "challenger-forward-replay",
        "title": "Challengers replayed on the issued-forecast cohort",
        "status": "shadow",
        "period": f"{season}/{str(season + 1)[2:]} GW{weeks[0]}-{weeks[-1]}, refitted before each",
        "summary": (
            f"Same {len(df):,} player-gameweeks as the forward results above. Direct points "
            f"challenger against the simulator: {vs_model.mean:+.3f} MAE ± "
            f"{(vs_model.se or float('nan')):.3f} ({verdict(vs_model, 'challenger error')}). "
            f"Against FPL's expected points: {vs_ep.mean:+.3f} ± {(vs_ep.se or float('nan')):.3f}. "
            + (
                f"Minutes challenger P(start) Brier against the simulator: {st.mean:+.4f} ± "
                f"{(st.se or float('nan')):.4f} ({verdict(st, 'challenger Brier')})."
                if st.mean is not None
                else "The issued forecasts of these gameweeks did not store P(start) (stored "
                "before the run registry), so the minutes comparison waits for newer weeks."
            )
            + f" A blend chosen on GW{half[0]}-{half[-1]} and tested on the rest: "
            f"{(blend_test.mean or 0):+.3f} MAE against the simulator "
            f"({verdict(blend_test, 'blend error')})."
        ),
        "metrics": {
            "vs_simulator": vs_model.row(),
            "vs_fpl_ep": vs_ep.row(),
            "blend_weight_on_simulator": w,
            "blend_test_vs_simulator": blend_test.row(),
            "start_brier_vs_simulator": st.row(),
            "seconds": round(time.time() - t0, 1),
        },
        "rows": rows,
        "decision": "stays in shadow: "
        + (
            "fewer gameweeks than the evidence policy needs"
            if vs_model.weeks < THRESHOLDS["min_gameweeks"] or vs_model.se is None
            else "promotion goes through the engine's learning gate, not this report"
        ),
    }


def run_all(db: FootballDB, season: int, gws: list[int]) -> list[dict[str, Any]]:
    """Every challenger report. Splits are chronological and fixed in advance: train 2022-23,
    tune 2024, test 2025 for the backtests; the forward replay refits before each gameweek."""
    pw = player_weeks(db, [2022, 2023, 2024, 2025, season])
    out = [
        points_backtest(pw, [2022, 2023], 2024, 2025),
        minutes_backtest(pw, [2022, 2023], 2024, 2025),
        forward_replay(db, pw, season, gws, [2022, 2023, 2024, 2025]),
    ]
    for r in out:
        r["generated_at"] = time.time()
        r["features"] = {
            "form": FORM,
            "state": STATE,
            "contribution": CONTRIB,
            "opponent": OPP,
            "workload": WORK,
        }
        r["gaps"] = [
            "FPL expected_goals includes penalties: npxG is not in the public data",
            "xG, xA, starts and FPL's recorded xP exist only from 2022/23",
            "no home/away flag per player-gameweek in older seasons",
            "no news or social signals (by design)",
            "ownership and FPL availability flags are missing from the older snapshots",
        ]
    return out


__all__ = [
    "player_weeks",
    "run_all",
    "points_backtest",
    "minutes_backtest",
    "forward_replay",
    "block",
    "verdict",
    "baseline_probs",
    "ARMS",
    "CLASSES",
]
