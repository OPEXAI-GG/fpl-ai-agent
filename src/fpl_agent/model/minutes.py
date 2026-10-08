"""L3 minutes model: for each player and fixture, P(start), P(60+ | start) and P(cameo).

Three gradient-boosted classifiers on form features (recent minutes and starts, zero-minute
streaks, rest, price rank within the team, how new the player is). Official availability is an
overlay, not a feature, because historical rows have no point-in-time injury news: a player FPL
flags as out gets ~0 now and a rising chance of returning, a doubtful player's chance of playing
multiplies the model's.

Certainty decays across the horizon: the forecast k gameweeks ahead moves from the model's
next-fixture view back toward the player's long-run rate, because injury and rotation risk
accumulate with distance.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.metrics import brier_score_loss, log_loss

from fpl_agent.model.features import MINUTES_FEATURES, minutes_features

EPS = 1e-4
# weekly chance a player flagged out comes back, by FPL status
RETURN_HAZARD = {"i": 0.12, "s": 0.5, "d": 0.4, "n": 0.0, "u": 0.0}


def availability_factor(status: str | None, chance: float | None, horizon: int) -> float:
    """Multiplier on P(play) from FPL's flag. 1.0 for an available player.

    `chance` is chance_of_playing_next_round (0-100, None when FPL gives none). Doubtful players
    play with that chance next week and recover toward 1; injured and suspended players come back
    at a weekly hazard; 'n'/'u' (left/unavailable) never do.
    """
    status = status or "a"
    if status == "a" and (chance is None or chance >= 100 or np.isnan(chance)):
        return 1.0
    first = 1.0 if chance is None or np.isnan(chance) else float(chance) / 100.0
    if status in ("n", "u"):
        return 0.0
    if status == "a":
        status = "d"  # an 'available' player with a reduced chance is doubtful
    hazard = RETURN_HAZARD.get(status, 0.3)
    if status in ("i", "s") and first >= 1.0:
        first = 0.0  # injured/suspended with no stated chance: assume out next week
    k = max(horizon - 1, 0)
    return float(1.0 - (1.0 - first) * (1.0 - hazard) ** k)


def _clf() -> HistGradientBoostingClassifier:
    return HistGradientBoostingClassifier(
        max_iter=150, learning_rate=0.06, max_depth=4, min_samples_leaf=40, l2_regularization=1.0,
        random_state=0,
    )  # fmt: skip


def training_rows(frame: pd.DataFrame) -> pd.DataFrame:
    """Rows with usable history: skip each veteran's first-ever row in the data window."""
    feat = minutes_features(frame)
    first_season = feat["season"].min()
    return feat[(feat["n_prev"] >= 1) | (feat["season"] > first_season)]


@dataclass
class MinutesModel:
    start: HistGradientBoostingClassifier | None = None
    long: HistGradientBoostingClassifier | None = None  # P(60+ | start)
    cameo: HistGradientBoostingClassifier | None = None  # P(any minutes | not start)
    cameo_minutes: float = 20.0
    base_rate: dict[int, float] = field(default_factory=dict)  # long-run P(start) per player
    pos_rate: dict[str, float] = field(default_factory=dict)  # position average P(start)
    decay: float = 0.85  # per gameweek beyond the next fixture
    cols: list[str] = field(default_factory=lambda: list(MINUTES_FEATURES))

    @classmethod
    def fit(cls, frame: pd.DataFrame, decay: float = 0.85) -> MinutesModel:
        feat = training_rows(frame)
        cols = [c for c in MINUTES_FEATURES if feat[c].notna().any()]  # all-NaN columns can't bin
        X, started = feat[cols], feat["started"].to_numpy()
        m = cls(decay=decay, cols=cols)
        m.start = _clf().fit(X, started)
        s = feat[started]
        m.long = _clf().fit(s[cols], (s["minutes"] >= 60).to_numpy())
        b = feat[~started]
        m.cameo = _clf().fit(b[cols], (b["minutes"] > 0).to_numpy())
        played_b = b[b["minutes"] > 0]["minutes"]
        m.cameo_minutes = float(played_b.mean()) if len(played_b) else 20.0
        # long-run rate from the last 30 rows, shrunk to the position average
        recent = frame.sort_values(["player_id", "season", "gw"]).groupby("player_id").tail(30)
        grp = recent.groupby("player_id")["started"].agg(["sum", "count"])
        m.pos_rate = frame.groupby("position")["started"].mean().to_dict()
        pos_of = frame.groupby("player_id")["position"].last()
        prior = pos_of.map(m.pos_rate).reindex(grp.index).fillna(0.3)
        k = 6.0
        m.base_rate = ((grp["sum"] + k * prior) / (grp["count"] + k)).to_dict()
        return m

    # ---- scoring ---------------------------------------------------------------------
    def predict(self, feat: pd.DataFrame) -> pd.DataFrame:
        """Probabilities for rows that already carry MINUTES_FEATURES."""
        X = feat[self.cols]
        return pd.DataFrame(
            {"p_start": np.clip(self.start.predict_proba(X)[:, 1], EPS, 1 - EPS),
             "p_long": np.clip(self.long.predict_proba(X)[:, 1], EPS, 1 - EPS),
             "p_cameo": np.clip(self.cameo.predict_proba(X)[:, 1], EPS, 1 - EPS)},
            index=feat.index,
        )  # fmt: skip

    def forecast(self, history: pd.DataFrame, upcoming: pd.DataFrame) -> pd.DataFrame:
        """Minutes probabilities for upcoming (player, fixture) rows.

        `history` is player_gw_frame up to now. `upcoming` has player_id, season, gw, horizon
        (1 = the next gameweek), team_id, position, price, status, chance, and kickoff. The model
        scores each player once as the "next row" of their history; later horizons decay toward
        the long-run rate; availability is overlaid last.
        """
        up = upcoming.sort_values(["player_id", "horizon", "gw"]).reset_index(drop=True)
        first = up.groupby("player_id", as_index=False).first()
        stub = first.assign(minutes=0, started=False, fixture_id=-1)
        known = history[history["player_id"].isin(stub["player_id"])]
        both = pd.concat([known.assign(_new=False), stub.assign(_new=True)], ignore_index=True)
        both = both.sort_values(["player_id", "_new", "season", "gw"], kind="stable")
        new_rows = both["_new"].to_numpy()  # each player's placeholder is their last row
        feat = minutes_features(both.drop(columns=["_new"]))[new_rows]
        probs = self.predict(feat)
        base = probs.assign(player_id=feat["player_id"].to_numpy())
        merged = up.merge(base, on="player_id", how="left")
        pos_default = merged["position"].map(self.pos_rate).fillna(0.3)
        long_run = merged["player_id"].map(self.base_rate).fillna(pos_default)
        k = (merged["horizon"] - 1).clip(lower=0)
        w = self.decay**k
        merged["p_start"] = long_run + (merged["p_start"] - long_run) * w
        merged["p_start"] = merged["p_start"].fillna(pos_default)
        merged["p_long"] = merged["p_long"].fillna(0.75)
        merged["p_cameo"] = merged["p_cameo"].fillna(0.15)
        fac = [availability_factor(s, c, h) for s, c, h in
               zip(merged["status"], merged["chance"], merged["horizon"], strict=True)]  # fmt: skip
        merged["availability"] = fac
        merged["p_start"] = (merged["p_start"] * merged["availability"]).clip(EPS, 1 - EPS)
        merged["p_cameo"] = (merged["p_cameo"] * merged["availability"]).clip(EPS, 1 - EPS)
        merged["cameo_minutes"] = self.cameo_minutes
        return merged[["player_id", "season", "gw", "horizon", "p_start", "p_long", "p_cameo",
                       "availability", "cameo_minutes"]]  # fmt: skip


# ---- evaluation ------------------------------------------------------------------------
def evaluate(frame: pd.DataFrame, holdout_season: int) -> dict[str, float]:
    """Train on earlier seasons, score the holdout season against the last-5-starts baseline."""
    train = frame[frame["season"] < holdout_season]
    test_all = training_rows(frame)
    test = test_all[test_all["season"] == holdout_season]
    model = MinutesModel.fit(train)
    probs = model.predict(test)
    y = test["started"].to_numpy()
    baseline = test["s5"].fillna(test["s5"].mean()).clip(0.02, 0.98).to_numpy()
    return {
        "n": float(len(test)),
        "logloss_model": float(log_loss(y, probs["p_start"], labels=[False, True])),
        "logloss_baseline": float(log_loss(y, baseline, labels=[False, True])),
        "brier_model": float(brier_score_loss(y, probs["p_start"])),
        "brier_baseline": float(brier_score_loss(y, baseline)),
        "mean_pred": float(probs["p_start"].mean()),
        "mean_actual": float(y.mean()),
    }


def calibration_table(p: np.ndarray, y: np.ndarray, bins: int = 10) -> pd.DataFrame:
    df = pd.DataFrame({"p": p, "y": y.astype(float)})
    df["bin"] = pd.qcut(df["p"], bins, duplicates="drop")
    return df.groupby("bin", observed=True).agg(pred=("p", "mean"), actual=("y", "mean"),
                                                n=("y", "size"))  # fmt: skip
