"""Bounded feature overlays on the minutes forecast, each behind its own flag.

* `FEATURE_NEWS_AVAILABILITY` (news evidence, `model.evidence`): moves the availability
  factor toward the evidence; at most 0.30 from FPL's own flag, never for status n/u, and the
  shift halves for each gameweek beyond the next (a reported return date applies exactly).
* `FEATURE_WORKLOAD` (rest and workload, `model.workload`): moves P(start) toward a model
  trained with workload features; at most 0.10, next gameweek only, and a player whose
  workload is unknown keeps the baseline.

Each flag is `off`, `shadow` or `on`:

* `off`: not computed.
* `shadow` (the default for both): the plan uses the baseline; a second forecast with the
  feature is stored beside it (`forecast_runs.variant = 'shadow'`) for forward evaluation.
* `on`: the plan uses the feature; the baseline is stored as the shadow.

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
from fpl_agent.model import evidence, workload
from fpl_agent.model.features import minutes_features
from fpl_agent.model.minutes import EPS, availability_factor

FEATURES = ("news_availability", "workload")
MODES = ("off", "shadow", "on")
NEWS_MAX_SHIFT = 0.30
NEWS_DECAY = 0.5
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


@dataclass
class NewsAvailability:
    db: FootballDB
    name: str = "news_availability"
    max_shift: float = NEWS_MAX_SHIFT
    changed: list[dict] = field(default_factory=list)
    counts: dict = field(default_factory=dict)

    def apply(self, mins: pd.DataFrame, upcoming: pd.DataFrame, cutoff: datetime | None,
              ctx: dict | None = None) -> pd.DataFrame:  # fmt: skip
        out = mins.copy()
        out["evidence"] = [[] for _ in range(len(out))]
        if cutoff is None:
            self.counts = {"note": "no cutoff: evidence not applied"}
            return out
        ev = evidence.player_evidence(self.db, cutoff)
        info = upcoming.drop_duplicates(["player_id", "gw"]).set_index(["player_id", "gw"])
        states: dict[str, int] = {}
        for i, r in out.iterrows():
            e = ev.get(int(r.player_id))
            if e is None:
                continue
            key = (r.player_id, r.gw)
            status = info.loc[key, "status"] if key in info.index else None
            kickoff = info.loc[key, "kickoff"] if key in info.index else None
            if status in ("n", "u"):
                states["blocked_by_fpl_status"] = states.get("blocked_by_fpl_status", 0) + 1
                continue
            if e.state != "applied":
                states[e.state] = states.get(e.state, 0) + 1
                out.at[i, "evidence"] = [{"state": e.state, "note": e.note, "claims": e.claims}]
                continue
            base = float(r.availability)
            h = int(r.horizon)
            target, decay = None, 1.0
            if (
                e.return_at is not None
                and kickoff is not None
                and not pd.isna(kickoff)
                and pd.Timestamp(kickoff) < pd.Timestamp(e.return_at)
            ):
                target = 0.0  # still before the reported return date: no decay
            elif e.p_play is not None:
                target = e.p_play
                decay = NEWS_DECAY ** max(h - 1, 0)
            if target is None:
                continue
            w = e.weight
            shift = float(np.clip(w * (target - base), -self.max_shift, self.max_shift)) * decay
            new = float(np.clip(base + shift, 0.0, 1.0))
            if abs(new - base) < 1e-6:
                continue
            out.at[i, "availability"] = new
            out.at[i, "p_start"] = float(np.clip(r.p_start_model * new, EPS, 1 - EPS))
            out.at[i, "p_cameo"] = float(np.clip(r.p_cameo_model * new, EPS, 1 - EPS))
            cite = {"state": "applied", "from": round(base, 3), "to": round(new, 3),
                    "weight": round(w, 3), "claims": e.claims}  # fmt: skip
            out.at[i, "evidence"] = [cite]
            states["applied"] = states.get("applied", 0) + 1
            if h == 1:
                self.changed.append({"player_id": int(r.player_id), **cite})
        self.counts = {**states, **{f"excluded_{k}": v
                                    for k, v in evidence.excluded_counts(self.db, cutoff).items()},
                       "players_with_evidence": len(ev)}  # fmt: skip
        return out

    def summary(self) -> dict:
        return {"changed_next_gw": len(self.changed), "counts": self.counts}


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
        wl = workload.workload_features(feat, apps, teams)
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
            self.model = WorkloadModel.fit(history, base.cols, apps, teams)
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


def build(names: list[str], db: FootballDB) -> list:
    made = {"news_availability": NewsAvailability, "workload": Workload}
    return [made[n](db) for n in names if n in made]


__all__ = ["modes", "build", "NewsAvailability", "Workload", "availability_factor"]
