"""The weekly learning loop: score what was issued, compare baseline and enriched forecasts on the
same players, and move a versioned learning state forward only on enough evidence.

After a gameweek is final (`pit.finality`):

1. **Score the issued forecasts** (`evaluate_gw`). The primary run is the newest one made before
   the deadline; its shadow is the run stored beside it with the other feature set. Both are
   scored on the identical cohort (players in both runs with an official result): points MAE,
   RMSE, bias; P(start) Brier and log loss and a 5-bin calibration table (single-fixture players
   only, `starts` from FPL); P(play) Brier; evidence coverage. Stored in `evaluations`.
2. **Propose a candidate state** (`weekly_update`). Calibration factors are re-estimated from
   final reviews only and each moves at most `MAX_CAL_STEP` from the current one. A shadow feature
   is promoted to `on` only when its paired forward evidence (players it changed, never mixed
   with players another feature changed) meets every threshold in `THRESHOLDS`; a promoted
   feature that regresses is put back to `shadow`.
3. **Compare candidate with current** on the newest final week, which the candidate's
   calibration was *not* estimated from (walk-forward: validate on the holdout week, then refit
   with it). The candidate becomes current only when it is no worse than `MAX_REGRESSION`;
   otherwise it is stored as `rejected` with its reason and the current state stays.
4. **Rollback** (`rollback`) makes an earlier version current again, recorded like any change.

Thresholds are fixed here, before any evaluation is run, and are reported with every decision.
Hindsight lessons about transfers (`lessons.py`) never count as evidence that a prediction
feature works; only forecast-versus-result comparisons do. Historical evaluation of features
that have history (workload) is `walk_forward`, with a tuning period kept apart from the final
test period.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

import numpy as np
import pandas as pd

from fpl_agent import pit
from fpl_agent.db.database import FootballDB, now
from fpl_agent.model import calibration, overlays

THRESHOLDS = {
    "min_gameweeks": 4,  # final gameweeks with both arms before a feature can be promoted
    "min_affected": 60,  # player-weeks the feature changed, across those gameweeks
    "se_multiple": 2.0,  # improvement must exceed this many standard errors (paired)
    "max_brier_regression": 0.0,  # P(start) Brier on affected players may not get worse
    "max_regression": 0.01,  # candidate may be at most this much worse in holdout MAE
    "min_calibration_weeks": 2,  # final reviews before calibration may change at all
}
MAX_CAL_STEP = 0.03
DEFAULT_CONFIG = {"calibration": {}, "features": {f: "shadow" for f in overlays.FEATURES}}


# ---- scoring one run -------------------------------------------------------------------------
def actuals(db: FootballDB, season: int, gw: int) -> pd.DataFrame:
    df = db.query(
        "SELECT player_id, sum(total_points) AS actual, sum(minutes) AS minutes, count(*) AS "
        "fixtures, max(TRY_CAST(json_extract_string(stats, '$.starts') AS DOUBLE)) AS starts "
        "FROM fpl_points_gw WHERE season = ? AND gw = ? AND player_id IS NOT NULL "
        "GROUP BY player_id",
        [season, gw],
    )
    df["started"] = np.where(df["starts"].notna(), df["starts"] > 0, df["minutes"] >= 60)
    return df


def run_frame(db: FootballDB, run_id: str, season: int, gw: int) -> pd.DataFrame:
    p = pit.run_projection(db, run_id, season, gw)
    det = [json.loads(d) if isinstance(d, str) else {} for d in p["detail"]]
    p = p.assign(
        p_start=[d.get("p_start") for d in det], p_cameo=[d.get("p_cameo") for d in det],
        news=[any(e.get("state") == "applied" for e in d.get("evidence") or []) for d in det],
        workload=[bool((d.get("workload") or {}).get("delta_p_start")) for d in det],
    )  # fmt: skip
    return p.drop(columns=["detail"])


def metrics(df: pd.DataFrame, col: str = "xpts") -> dict[str, Any]:
    if df.empty:
        return {"n": 0}
    err = df[col] - df["actual"]
    out = {
        "n": int(len(df)),
        "mae": round(float(err.abs().mean()), 4),
        "rmse": round(float(np.sqrt((err**2).mean())), 4),
        "bias": round(float(err.mean()), 4),
    }
    single = df[(df["fixtures"] == 1) & df["p_start"].notna()]
    if len(single):
        p = single["p_start"].astype(float).clip(1e-4, 1 - 1e-4)
        y = single["started"].astype(float)
        out["start_brier"] = round(float(((p - y) ** 2).mean()), 4)
        out["start_logloss"] = round(float(-(y * np.log(p) + (1 - y) * np.log(1 - p)).mean()), 4)
        pc = single["p_cameo"].astype(float).fillna(0)
        play = (p + (1 - p) * pc).clip(0, 1)
        out["play_brier"] = round(float(((play - (single["minutes"] > 0)) ** 2).mean()), 4)
        bins = pd.cut(p, [0, 0.2, 0.4, 0.6, 0.8, 1.0], include_lowest=True)
        out["start_calibration"] = [
            {"bin": str(b), "n": int(len(g)), "pred": round(float(g.mean()), 3),
             "actual": round(float(y[g.index].mean()), 3)}
            for b, g in p.groupby(bins, observed=True)]  # fmt: skip
    return out


def _paired_runs(db: FootballDB, season: int, gw: int):
    prim = pit.eligible_run(db, season, gw, "primary")
    if not prim.found or prim.legacy:
        return prim, None
    row = db.con.execute(
        "SELECT run_id, flags FROM forecast_runs WHERE parent_run_id = ? AND variant = 'shadow' "
        "ORDER BY created_at DESC LIMIT 1", [prim.run_id]).fetchone()  # fmt: skip
    return prim, row


def evaluate_gw(db: FootballDB, season: int, gw: int) -> dict[str, Any]:
    fin = pit.finality(db, season, gw)
    if not fin.final:
        return {"gw": gw, "final": False, "reason": fin.reason}
    prim, shadow = _paired_runs(db, season, gw)
    if not prim.found:
        return {"gw": gw, "final": True, "available": False, "reason": prim.reason}
    act = actuals(db, season, gw)
    a = run_frame(db, prim.run_id, season, gw).merge(act, on="player_id")
    arms = {(prim.flags or {}).get("arm", "baseline"): (prim.run_id, a)}
    cal = (prim.flags or {}).get("calibration") or {}
    pos = db.query("SELECT player_id, position FROM players")
    if shadow:
        sflags = json.loads(shadow[1]) if shadow[1] else {}
        b = run_frame(db, shadow[0], season, gw).merge(act, on="player_id")
        arms[sflags.get("arm", "enriched")] = (shadow[0], b)
    cohort = set.intersection(*(set(f["player_id"]) for _, f in arms.values()))
    out: dict[str, Any] = {"gw": gw, "final": True, "available": True, "cohort": len(cohort),
                           "primary_run": prim.run_id, "arms": {}}  # fmt: skip
    rows = []
    for arm, (run_id, f) in arms.items():
        f = f[f["player_id"].isin(cohort)]
        m = metrics(f)
        if run_id == prim.run_id and cal:  # what the plan used: raw x its position factor
            g = f.merge(pos, on="player_id", how="left")
            g = g.assign(issued=g["xpts"] * g["position"].map(cal).fillna(1.0))
            m["issued"] = {k: v for k, v in metrics(g, "issued").items()
                           if k in ("n", "mae", "rmse", "bias")}  # fmt: skip
        m["evidence_coverage"] = round(float(f["news"].mean()), 4) if len(f) else 0.0
        m["workload_coverage"] = round(float(f["workload"].mean()), 4) if len(f) else 0.0
        out["arms"][arm] = {"run_id": run_id, **m}
        rows.append({"season": season, "gw": gw, "variant": arm, "run_id": run_id,
                     "cohort": "identical", "n": m["n"], "metrics": json.dumps(m),
                     "created_at": now()})  # fmt: skip
    if len(arms) == 2:
        base, enr = arms.get("baseline"), arms.get("enriched")
        if base and enr:
            pair = base[1][["player_id", "xpts", "actual", "news", "workload"]].merge(
                enr[1][["player_id", "xpts", "news", "workload"]], on="player_id",
                suffixes=("_b", "_e"))  # fmt: skip
            pair["d_abs"] = (pair["xpts_e"] - pair["actual"]).abs() - (
                pair["xpts_b"] - pair["actual"]).abs()  # fmt: skip
            keep = ["player_id", "d_abs", "news_b", "news_e", "workload_b", "workload_e"]
            out["paired"] = pair[keep].to_dict("records")
    db.upsert("evaluations", pd.DataFrame(rows))
    db.con.execute(
        "DELETE FROM reviews WHERE season = ? AND gw = ? AND kind = 'evaluation'", [season, gw]
    )
    db.append("reviews", pd.DataFrame([{"season": season, "gw": gw, "created_at": now(),
                                        "kind": "evaluation",
                                        "payload": json.dumps(out, default=str)}]))  # fmt: skip
    return out


# ---- the learning state ----------------------------------------------------------------------
def current_state(db: FootballDB) -> dict[str, Any] | None:
    r = db.query("SELECT * FROM learning_state WHERE status = 'current' ORDER BY version DESC "
                 "LIMIT 1")  # fmt: skip
    if r.empty:
        return None
    d = r.iloc[0].to_dict()
    d["config"] = json.loads(d["config"]) if d.get("config") else dict(DEFAULT_CONFIG)
    return d


def history(db: FootballDB) -> pd.DataFrame:
    return db.query("SELECT version, created_at, status, parent_version, season, through_gw, "
                    "training_cutoff, reason FROM learning_state ORDER BY version")  # fmt: skip


def _store_state(db: FootballDB, status: str, parent: int | None, season: int, gw: int | None,
                 cutoff: datetime | None, config: dict, samples: dict, evaluation: dict,
                 reason: str) -> int:  # fmt: skip
    v = db.con.execute("SELECT coalesce(max(version), 0) + 1 FROM learning_state").fetchone()[0]
    if status == "current":
        db.con.execute("UPDATE learning_state SET status = 'retired' WHERE status = 'current'")
    db.append("learning_state", pd.DataFrame([{
        "version": v, "created_at": now(), "status": status, "parent_version": parent,
        "season": season, "through_gw": gw, "training_cutoff": cutoff,
        "config": json.dumps(config), "samples": json.dumps(samples),
        "evaluation": json.dumps(evaluation, default=str), "reason": reason}]))  # fmt: skip
    return int(v)


def _feature_evidence(db: FootballDB, season: int, through_gw: int) -> dict[str, dict]:
    """Paired forward evidence per feature over every evaluated final gameweek."""
    rows = db.query("SELECT gw, payload FROM reviews WHERE season = ? AND kind = 'evaluation' "
                    "AND gw <= ? ORDER BY gw", [season, through_gw])  # fmt: skip
    out = {}
    for feat, own, other in (
        ("news_availability", "news", "workload"),
        ("workload", "workload", "news"),
    ):
        diffs, weeks, brier = [], set(), []
        for gw, blob in zip(rows["gw"], rows["payload"], strict=True):
            p = json.loads(blob)
            # players this feature changed in the enriched arm only, and no other feature did
            pairs = [x for x in p.get("paired") or []
                     if x.get(f"{own}_e") and not x.get(f"{own}_b")
                     and x.get(f"{other}_e") == x.get(f"{other}_b")]  # fmt: skip
            if pairs:
                weeks.add(int(gw))
                diffs += [float(x["d_abs"]) for x in pairs]
            arms = p.get("arms") or {}
            if "baseline" in arms and "enriched" in arms and pairs:
                b, e = arms["baseline"].get("start_brier"), arms["enriched"].get("start_brier")
                if b is not None and e is not None:
                    brier.append(e - b)
        n = len(diffs)
        mean = float(np.mean(diffs)) if n else None
        se = float(np.std(diffs, ddof=1) / np.sqrt(n)) if n > 1 else None
        out[feat] = {
            "gameweeks": len(weeks),
            "affected": n,
            "mean_abs_error_change": mean,
            "se": se,
            "start_brier_change": float(np.mean(brier)) if brier else None,
        }
    return out


def decide_feature(ev: dict, mode: str) -> tuple[str, str]:
    """(new mode, reason) under the fixed THRESHOLDS."""
    t = THRESHOLDS
    if mode == "off":
        return "off", "off by configuration"
    enough = ev["gameweeks"] >= t["min_gameweeks"] and ev["affected"] >= t["min_affected"]
    if not enough or ev["mean_abs_error_change"] is None or ev["se"] is None:
        return mode, (
            f"insufficient evidence ({ev['gameweeks']} of {t['min_gameweeks']} "
            f"gameweeks, {ev['affected']} of {t['min_affected']} player-weeks)"
        )
    m, se = ev["mean_abs_error_change"], ev["se"]
    brier_ok = ev["start_brier_change"] is None or ev["start_brier_change"] <= t[
        "max_brier_regression"]  # fmt: skip
    if mode == "shadow" and m + t["se_multiple"] * se < 0 and brier_ok:
        return "on", f"promoted: abs error {m:+.3f} +- {se:.3f} per affected player-week"
    if mode == "on" and m - t["se_multiple"] * se > 0:
        return "shadow", f"demoted: abs error {m:+.3f} +- {se:.3f} per affected player-week"
    return mode, f"no significant change: abs error {m:+.3f} +- {se:.3f}"


def _holdout_mae(db: FootballDB, season: int, gw: int, factors: dict[str, float]) -> float | None:
    prim, _ = _paired_runs(db, season, gw)
    if not prim.found:
        return None
    f = run_frame(db, prim.run_id, season, gw).merge(actuals(db, season, gw), on="player_id")
    f = f.merge(db.query("SELECT player_id, position FROM players"), on="player_id", how="left")
    if f.empty:
        return None
    pred = f["xpts"] * f["position"].map(factors).fillna(1.0)
    return float((pred - f["actual"]).abs().mean())


@dataclass
class UpdateResult:
    status: str  # not_final | promoted | retained | rejected
    version: int | None
    reason: str
    evaluation: dict = field(default_factory=dict)

    def text(self) -> str:
        return f"learning: {self.status} (version {self.version}): {self.reason}"


def weekly_update(db: FootballDB, season: int, gw: int) -> UpdateResult:
    fin = pit.finality(db, season, gw)
    if not fin.final:
        return UpdateResult("not_final", None, f"GW{gw} is provisional: {fin.reason}")
    ev = evaluate_gw(db, season, gw)
    cur = current_state(db)
    cur_cfg = cur["config"] if cur else json.loads(json.dumps(DEFAULT_CONFIG))
    cur_cal: dict[str, float] = cur_cfg.get("calibration") or {}
    # calibration: validate the procedure on the holdout week (estimated without it), then refit
    n_reviews = sum(
        1 for b in db.query("SELECT payload FROM reviews WHERE season = ? AND kind = 'prediction' "
                            "AND gw <= ?", [season, gw])["payload"]
        if calibration.usable(json.loads(b)))  # fmt: skip
    val_fac = _bounded(cur_cal, calibration.factors(db, season, gw))
    new_fac = _bounded(cur_cal, calibration.factors(db, season, gw + 1))
    if n_reviews < THRESHOLDS["min_calibration_weeks"]:
        new_fac, val_fac = dict(cur_cal), dict(cur_cal)
    mae_cur, mae_cand = _holdout_mae(db, season, gw, cur_cal), _holdout_mae(db, season, gw, val_fac)
    feats = _feature_evidence(db, season, gw)
    new_modes, reasons = {}, {}
    for f in overlays.FEATURES:
        new_modes[f], reasons[f] = decide_feature(feats[f], (cur_cfg.get("features") or {}).get(
            f, "shadow"))  # fmt: skip
    cand = {"calibration": new_fac, "features": new_modes}
    evaluation = {"holdout_gw": gw, "holdout_mae_current": mae_cur,
                  "holdout_mae_candidate": mae_cand, "features": feats, "decisions": reasons,
                  "thresholds": THRESHOLDS, "weekly": ev}  # fmt: skip
    samples = {"final_reviews": n_reviews, **{f"{k}_affected": v["affected"]
                                              for k, v in feats.items()}}  # fmt: skip
    cutoff = pit.deadline(db, season, gw + 1).at
    parent = cur["version"] if cur else None
    if cand == cur_cfg:
        return UpdateResult("retained", parent, "no change proposed: " + "; ".join(
            f"{k}: {v}" for k, v in reasons.items()), evaluation)  # fmt: skip
    worse = (mae_cur is not None and mae_cand is not None
             and mae_cand > mae_cur + THRESHOLDS["max_regression"])  # fmt: skip
    if worse:
        v = _store_state(db, "rejected", parent, season, gw, cutoff, cand, samples, evaluation,
                         f"holdout MAE {mae_cand:.3f} vs current {mae_cur:.3f}")  # fmt: skip
        return UpdateResult("rejected", v, "candidate worse on the holdout week; current kept",
                            evaluation)  # fmt: skip
    v = _store_state(db, "current", parent, season, gw, cutoff, cand, samples, evaluation,
                     "; ".join(f"{k}: {r}" for k, r in reasons.items()))  # fmt: skip
    return UpdateResult("promoted", v, f"candidate adopted (holdout MAE {mae_cand} vs {mae_cur})",
                        evaluation)  # fmt: skip


def _bounded(cur: dict[str, float], new: dict[str, float]) -> dict[str, float]:
    out = {}
    for pos in set(cur) | set(new):
        c, n = cur.get(pos, 1.0), new.get(pos, 1.0)
        out[pos] = round(float(np.clip(n, c - MAX_CAL_STEP, c + MAX_CAL_STEP)), 4)
    return {k: v for k, v in out.items() if v != 1.0}


def rollback(db: FootballDB, to_version: int | None = None) -> UpdateResult:
    cur = current_state(db)
    target = to_version if to_version is not None else (cur or {}).get("parent_version")
    if target is None:
        if cur is None:
            return UpdateResult("retained", None, "nothing to roll back")
        db.con.execute("UPDATE learning_state SET status = 'rolled_back' WHERE status = 'current'")
        return UpdateResult("rolled_back", None, "back to the built-in defaults")
    row = db.query("SELECT * FROM learning_state WHERE version = ?", [target])
    if row.empty:
        return UpdateResult("retained", cur["version"] if cur else None, f"no version {target}")
    db.con.execute("UPDATE learning_state SET status = 'rolled_back' WHERE status = 'current'")
    r = row.iloc[0]
    v = _store_state(db, "current", int(r["version"]), int(r["season"]),
                     None if pd.isna(r["through_gw"]) else int(r["through_gw"]),
                     None if pd.isna(r["training_cutoff"]) else r["training_cutoff"],
                     json.loads(r["config"]), json.loads(r["samples"] or "{}"),
                     {"rollback_of": cur["version"] if cur else None},
                     f"rollback to version {target}")  # fmt: skip
    return UpdateResult("rolled_back", v, f"version {target} is current again (as {v})")


# ---- historical walk-forward for features with history (workload) -----------------------------
def walk_forward(
    db: FootballDB,
    season: int,
    gws: list[int],
    bounds: tuple[float, ...] = (0.05, 0.10, 0.15),
    tune_share: float = 0.5,
    refit_every: int = 4,
    ablations: dict[str, list[str]] | None = None,
) -> dict[str, Any]:
    """Chronological evaluation of the bounded workload nudge on P(start).

    For each gameweek the models are fitted on rows before it only; the deadline is the cutoff
    for workload facts. The first `tune_share` of gameweeks chooses the bound; the remaining
    gameweeks are the final test, scored once with that bound and never used to choose anything.
    Ablations refit the workload model with subsets of the workload features.
    """
    from fpl_agent.model import features, minutes, workload

    ablations = ablations or {"all": list(workload.FEATURES),
                              "rest_only": ["xc_rest_days"],
                              "no_team_congestion": ["xc_rest_days", "xc_mins_7d",
                                                     "xc_mins_14d"]}  # fmt: skip
    frame = features.player_gw_frame(db)
    if frame.empty:
        return {"available": False, "reason": "not enough history or no gameweeks to test"}
    apps, teams = workload.appearance_log(db), workload.team_log(db)
    all_rows = minutes.training_rows(frame)
    per_gw, models, fitted_at = [], {}, None
    for gw in gws:
        hist = frame[
            (frame["season"] < season) | ((frame["season"] == season) & (frame["gw"] < gw))
        ]
        test = all_rows[(all_rows["season"] == season) & (all_rows["gw"] == gw)
                        & all_rows["kickoff"].notna()]  # fmt: skip
        if len(hist) < 500 or test.empty:
            continue
        if fitted_at is None or gw - fitted_at >= refit_every:
            base = minutes.MinutesModel.fit(hist)
            feat = minutes.training_rows(hist)
            feat = feat[feat["kickoff"].notna()]
            wl_train = workload.workload_features(feat, apps, teams)
            models = {"base": base}
            for name, cols in ablations.items():
                models[name] = _fit_subset(feat, wl_train, base.cols, cols)
            fitted_at = gw
        base = models["base"]
        cutoff = pit.deadline(db, season, gw).at
        wl = workload.workload_features(test, apps, teams, cutoff=cutoff)
        known = wl.notna().any(axis=1).to_numpy()
        y = test["started"].to_numpy(float)
        pb = base.predict(test)["p_start"].to_numpy()
        rec = {"gw": gw, "n": int(len(test)), "known": int(known.sum()),
               "baseline": _scores(pb, y)}  # fmt: skip
        for name in ablations:
            pe = models[name].predict(test[base.cols], wl)
            for b in bounds:
                adj = np.where(known, pb + np.clip(pe - pb, -b, b), pb).clip(1e-4, 1 - 1e-4)
                rec[f"{name}@{b}"] = _scores(adj, y)
        per_gw.append(rec)
    if not per_gw:
        return {"available": False, "reason": "not enough history or no gameweeks to test"}
    k = max(1, int(len(per_gw) * tune_share))
    tune, test_weeks = per_gw[:k], per_gw[k:]
    arms = [a for a in per_gw[0] if "@" in a]
    tuned = {a: _pool(tune, a) for a in arms}
    best = {}
    for name in ablations:
        cands = {a: v for a, v in tuned.items() if a.startswith(f"{name}@")}
        best[name] = min(cands, key=lambda a: cands[a]["brier"])
    result = {
        "available": True, "season": season, "gameweeks": [r["gw"] for r in per_gw],
        "tune_gameweeks": [r["gw"] for r in tune], "test_gameweeks": [r["gw"] for r in test_weeks],
        "tune": {"baseline": _pool(tune, "baseline"), **tuned}, "chosen": best,
        "test": ({"baseline": _pool(test_weeks, "baseline"),
                  **{name: {"arm": a, **_pool(test_weeks, a)} for name, a in best.items()}}
                 if test_weeks else None),
        "coverage": round(sum(r["known"] for r in per_gw) / sum(r["n"] for r in per_gw), 4),
        "limitations": [
            "minutes-level only (P(start) Brier and log loss); points need `backtest`",
            "domestic cup matches are not in the database, so their workload is invisible",
            "FPL archive seasons carry no availability flags: rows of injured players are "
            "included, which inflates every arm's error equally",
        ],
    }  # fmt: skip
    return result


def _fit_subset(feat: pd.DataFrame, wl: pd.DataFrame, base_cols: list[str], cols: list[str]):
    from fpl_agent.model.overlays import WorkloadModel, _clf

    use = [*base_cols, *[c for c in cols if wl[c].notna().any()]]
    X = pd.concat([feat[base_cols], wl[cols]], axis=1)
    clf = _clf().fit(X[use], feat["started"].to_numpy())
    return WorkloadModel(clf, use, len(feat), float(wl[cols].notna().any(axis=1).mean()))


def _scores(p: np.ndarray, y: np.ndarray) -> dict[str, float]:
    p = np.clip(p, 1e-4, 1 - 1e-4)
    return {"n": int(len(y)), "brier_sum": float(((p - y) ** 2).sum()),
            "logloss_sum": float(-(y * np.log(p) + (1 - y) * np.log(1 - p)).sum())}  # fmt: skip


def _pool(weeks: list[dict], arm: str) -> dict[str, float]:
    n = sum(w[arm]["n"] for w in weeks)
    return {"n": n, "brier": round(sum(w[arm]["brier_sum"] for w in weeks) / max(n, 1), 5),
            "logloss": round(sum(w[arm]["logloss_sum"] for w in weeks) / max(n, 1), 5)}  # fmt: skip
