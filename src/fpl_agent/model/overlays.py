"""Bounded feature overlays on the minutes forecast, each behind its own flag.

* `FEATURE_WORKLOAD` (rest and workload, `model.workload`): moves P(start) toward a model
  trained with workload features; at most 0.10, next gameweek only, and a player whose
  workload is unknown keeps the baseline.

Each flag is `off`, `shadow` or `on`:

* `off`: not computed.
* `shadow` (the default): the plan does not use it; an experiment arm with the
  feature added to the issued configuration is stored beside the plan's run
  (`forecast_runs.variant = 'experiment'`, see `agent.experiment_arms`) for forward evaluation.
* `on`: the plan uses it; an experiment arm with the feature removed is stored, so evidence
  keeps accumulating after promotion and a regression can demote it.

An env value wins; otherwise the current learning state may promote a feature to `on` after
it passed its evaluation (`learning.py`); otherwise `shadow`. Hard eligibility is never touched:
FPL's status, the rails and execution still read FPL's own flags, and an overlay changes only
the probabilities the simulator uses. Every overlay records what it changed and why.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import datetime

import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier

from fpl_agent.db.database import FootballDB
from fpl_agent.model import workload
from fpl_agent.model.features import minutes_features
from fpl_agent.model.minutes import EPS, availability_factor
from fpl_agent.pit import DEADLINE_LEAD

MODES = ("off", "shadow", "on")
WORKLOAD_BOUND = 0.10


def modes(promoted: dict[str, str] | None = None, env=None) -> dict[str, str]:
    env = os.environ if env is None else env
    out = {}
    for f in FEATURES:
        v = str(env.get(f"FEATURE_{f.upper()}", "")).strip().lower()
        if v in MODES:
            out[f] = v
        elif promoted and promoted.get(f) in MODES:
            out[f] = promoted[f]
        else:
            out[f] = "shadow"
    return out


# ---- workload --------------------------------------------------------------------------------
def _clf() -> HistGradientBoostingClassifier:
    return HistGradientBoostingClassifier(max_iter=150, learning_rate=0.06, max_depth=4,
                                          min_samples_leaf=40, l2_regularization=1.0,
                                          random_state=0)  # fmt: skip


@dataclass
class WorkloadModel:
    """P(start) from the baseline features plus workload; used only as a bounded nudge."""

    clf: HistGradientBoostingClassifier
    cols: list[str]
    n_train: int
    coverage: float  # share of training rows with any workload value

    @classmethod
    def fit(cls, history: pd.DataFrame, base_cols: list[str], apps: pd.DataFrame,
            teams: pd.DataFrame) -> WorkloadModel:  # fmt: skip
        from fpl_agent.model.minutes import training_rows

        feat = training_rows(history)
        feat = feat[feat["kickoff"].notna()]
        est = feat.groupby(["season", "gw"])["kickoff"].transform("min")
        row_deadline = (pd.to_datetime(est) - DEADLINE_LEAD).to_numpy("datetime64[ns]")
        wl = workload.workload_features(feat, apps, teams, cutoff=row_deadline)
        X = pd.concat([feat[base_cols], wl], axis=1)
        cols = [*base_cols, *[c for c in workload.FEATURES if wl[c].notna().any()]]
        clf = _clf().fit(X[cols], feat["started"].to_numpy())
        cov = float(wl.notna().any(axis=1).mean()) if len(wl) else 0.0
        return cls(clf, cols, len(feat), cov)

    def predict(self, feat: pd.DataFrame, wl: pd.DataFrame) -> np.ndarray:
        X = pd.concat([feat.reset_index(drop=True), wl.reset_index(drop=True)], axis=1)
        for c in self.cols:
            if c not in X:
                X[c] = np.nan
        return self.clf.predict_proba(X[self.cols])[:, 1]


@dataclass
class Workload:
    db: FootballDB
    name: str = "workload"
    bound: float = WORKLOAD_BOUND
    model: WorkloadModel | None = None
    changed: int = 0
    counts: dict = field(default_factory=dict)
    cache: dict = field(default_factory=dict, repr=False)
    features: pd.DataFrame | None = field(default=None, repr=False)  # inputs, next gameweek

    def apply(self, mins: pd.DataFrame, upcoming: pd.DataFrame, cutoff: datetime | None,
              ctx: dict | None = None) -> pd.DataFrame:  # fmt: skip
        ctx = ctx or {}
        out = mins.copy()
        out["workload"] = [{} for _ in range(len(out))]
        history, base = ctx.get("history"), ctx.get("minutes_model")
        if history is None or base is None or history.empty:
            self.counts = {"note": "no history: workload not applied"}
            return out
        apps, teams = workload.appearance_log(self.db), workload.team_log(self.db)
        if apps.empty:
            self.counts = {"note": "no cross-competition appearances: workload not applied"}
            return out
        if self.model is None:
            self.model = self.cache.get("workload_model")
        if self.model is None:
            self.model = WorkloadModel.fit(history, base.cols, apps, teams)
            self.cache["workload_model"] = self.model
        nxt = upcoming[upcoming["horizon"] == 1].drop_duplicates("player_id")
        if nxt.empty:
            return out
        stub = nxt.assign(minutes=0, started=False, fixture_id=-1)
        known = history[history["player_id"].isin(stub["player_id"])]
        both = pd.concat([known.assign(_new=False), stub.assign(_new=True)], ignore_index=True)
        both = both.sort_values(["player_id", "_new", "season", "gw"], kind="stable")
        new_rows = both["_new"].to_numpy()
        feat = minutes_features(both.drop(columns=["_new"]))[new_rows]
        wl = workload.workload_features(feat, apps, teams, cutoff=cutoff)
        self.features = pd.concat([feat[["player_id", "team_id", "kickoff"]].reset_index(drop=True),
                                   wl.reset_index(drop=True)], axis=1)  # fmt: skip
        known_wl = wl.notna().any(axis=1).to_numpy()
        enriched = self.model.predict(feat[base.cols], wl)
        baseline = base.predict(feat)["p_start"].to_numpy()
        delta = np.clip(enriched - baseline, -self.bound, self.bound)
        delta = np.where(known_wl, delta, 0.0)  # unknown workload: keep the baseline
        by_pid = dict(zip(feat["player_id"].astype(int), range(len(feat)), strict=True))
        for i, r in out[out["horizon"] == 1].iterrows():
            j = by_pid.get(int(r.player_id))
            if j is None or delta[j] == 0.0:
                continue
            raw = float(np.clip(r.p_start_model + delta[j], EPS, 1 - EPS))
            out.at[i, "p_start"] = float(np.clip(raw * r.availability, EPS, 1 - EPS))
            out.at[i, "workload"] = {"delta_p_start": round(float(delta[j]), 4),
                                     **{k: (None if pd.isna(v) else round(float(v), 1))
                                        for k, v in wl.iloc[j].items()}}  # fmt: skip
            self.changed += 1
        self.counts = {"players_next_gw": int(len(feat)), "with_workload": int(known_wl.sum()),
                       "changed": self.changed, "train_rows": self.model.n_train,
                       "train_coverage": round(self.model.coverage, 3)}  # fmt: skip
        return out

    def summary(self) -> dict:
        return self.counts


# Each overlay class takes (db, cache=...) and records what it changed for a player in a column
# named after the feature, which the saved detail and the learning loop read back.
REGISTRY: dict[str, type] = {"workload": Workload}
FEATURES: tuple[str, ...] = tuple(REGISTRY)


def build(names: list[str], db: FootballDB, cache: dict | None = None) -> list:
    """Fresh overlay instances for `names`. A shared `cache` lets several builds of the same run
    (the primary and its experiment arms) reuse one fitted workload model, so every arm applies
    the identical feature."""
    cache = cache if cache is not None else {}
    return [REGISTRY[n](db, cache=cache) for n in names if n in REGISTRY]


__all__ = ["modes", "build", "Workload", "availability_factor", "REGISTRY", "FEATURES"]
