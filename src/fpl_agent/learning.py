"""The weekly learning loop: score what was issued, compare each feature on matched experiment
arms, and move a versioned learning state forward only on enough, valid, new evidence.

After a gameweek is final (`pit.finality`):

1. **Score the issued forecasts** (`evaluate_gw`). The primary run is the newest one made before
   the deadline (`pit.eligible_run`). Three things are scored on official results:

   * `raw`: the primary run as saved (before calibration and the template correction), which
     is what calibration and reviews learn from;
   * `issued`: the run's `issued` variant, the forecast exactly as the planner received it
     after every transformation (calibration, template correction);
   * the **experiment arms** (`agent.experiment_arms`): the next gameweek re-simulated with the
     primary's models, sample count and seeds, once with the issued configuration (`control`,
     the null intervention, which must reproduce the primary exactly) and once with each
     shadow feature added / each `on` feature removed. A feature's comparison is the pair of
     arms that differ in that feature only, scored on the players both arms hold and the
     feature changed (`affected`): the change in absolute points error and, on the same
     players, the change in P(start) Brier score.

   Every arm is validated against the primary (`check_arm`): made before the deadline with a
   cutoff no later than it, the same season and gameweek, kind, model, model and feature
   versions, samples and seed, and a passed null-intervention check. An arm that fails is
   listed under `excluded` and supplies no evidence.
2. **Propose a candidate state** (`weekly_update`). Calibration factors are re-estimated from
   final reviews only and each moves at most `MAX_CAL_STEP` from the state the update starts
   from. A shadow feature is promoted when its comparisons since its mode last changed meet
   every threshold in `THRESHOLDS`, including a Brier guard on the same affected players; an
   `on` feature is demoted when its comparisons (the arm without it) show it hurts.
3. **Compare candidate with current** on the newest final week, which the candidate's
   calibration was *not* estimated from. Missing validation never authorises a change.
4. **Exactly once per evidence.** Each update records an evidence key (season, gameweek, the
   evaluation and review payloads it read, the actual results, versions and thresholds) in
   `learning_updates`. The same key again changes nothing, so a repeated call can neither
   compound calibration nor undo a rollback. A changed key for the latest processed gameweek
   (FPL corrected the results, a run was added) is a **correction**: when the state that
   update produced is still current, the candidate is recomputed from the state *before* it,
   so the weekly bound applies once; otherwise (rolled back, or a later week was processed)
   the correction is recorded and the state is left alone.
5. **Rollback** (`rollback`) makes an earlier version current again, recorded like any change.

Thresholds are fixed here, before any evaluation is run, and are reported with every decision.
Hindsight lessons about transfers (`lessons.py`) never count as evidence that a prediction
feature works; only forecast-versus-result comparisons do. Historical evaluation of features
that have history (workload) is `walk_forward`, with a tuning period kept apart from the final
test period and every test forecast made from what was known at that gameweek's deadline.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

import numpy as np
import pandas as pd

from fpl_agent import pit
from fpl_agent.db.database import FootballDB, now
from fpl_agent.model import calibration, overlays

LEARNING_VERSION = "learn-2026.11.3"  # .3: weekly-block check on promotion
THRESHOLDS = {
    "min_gameweeks": 4,  # final gameweeks with a valid comparison before a mode can change
    "min_affected": 60,  # player-weeks the feature changed, across those gameweeks
    "se_multiple": 2.0,  # improvement must exceed this many standard errors (paired)
    "max_brier_regression": 0.0,  # P(start) Brier on the affected players may not get worse
    "max_regression": 0.01,  # candidate may be at most this much worse in holdout MAE
    "min_calibration_weeks": 2,  # final reviews before calibration may change at all
}
MAX_CAL_STEP = 0.03


def default_config() -> dict[str, Any]:
    return {"calibration": {}, "features": {f: "shadow" for f in overlays.FEATURES}, "since": {}}


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
        # one column per feature: True where that overlay changed the player's forecast
        **{f: [bool(d.get(f)) for d in det] for f in overlays.FEATURES},
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


def _run_row(db: FootballDB, run_id: str) -> dict[str, Any] | None:
    df = db.query("SELECT * FROM forecast_runs WHERE run_id = ?", [run_id])
    if df.empty:
        return None
    d = df.iloc[0].to_dict()
    d["flags"] = json.loads(d["flags"]) if d.get("flags") else {}
    return d


def _ts(v) -> datetime | None:
    return None if v is None or pd.isna(v) else pit.utc(pd.Timestamp(v))


def check_arm(arm: dict, prim: dict, season: int, gw: int, deadline: datetime | None,
              matched: bool = True) -> str | None:  # fmt: skip
    """Why `arm` may not be compared with the primary run `prim` for (season, gw), or None."""
    made, cut = _ts(arm.get("created_at")), _ts(arm.get("cutoff"))
    if arm.get("parent_run_id") != prim["run_id"]:
        return "not an arm of the issued run"
    if int(arm.get("season") or -1) != season or str(gw) not in str(arm.get("gws")).split(","):
        return "another season or gameweek"
    if deadline is None or made is None or not made < deadline:
        return "made at or after the deadline"
    if cut is None or cut > deadline:
        return "information cutoff after the deadline"
    if not bool(arm.get("eligible")):
        return "registered as ineligible"
    for k in ("kind", "model", "model_version", "feature_version"):
        if arm.get(k) != prim.get(k):
            return f"{k} differs from the issued run ({arm.get(k)} vs {prim.get(k)})"
    if matched:
        f, pf = arm["flags"], prim["flags"]
        if f.get("samples") != pf.get("samples") or f.get("seed") != pf.get("seed"):
            return "simulation samples or seed differ from the issued run"
        if not (f.get("null_check") or {}).get("passed"):
            return "null-intervention check failed or missing"
    return None


def _children(db: FootballDB, run_id: str) -> list[dict]:
    df = db.query("SELECT run_id FROM forecast_runs WHERE parent_run_id = ? ORDER BY created_at, "
                  "run_id", [run_id])  # fmt: skip
    return [r for r in (_run_row(db, x) for x in df["run_id"]) if r]


def _compare(a: pd.DataFrame, b: pd.DataFrame, flag: str) -> dict[str, Any]:
    """Paired comparison of arm `a` (with the feature) and `b` (without) on the players both
    hold; `affected` = players the feature changed in `a`. The Brier change is computed on the
    same affected players (single-fixture ones, where `started` is defined per fixture)."""
    a, b = a.set_index("player_id"), b.set_index("player_id")
    cohort = a.index.intersection(b.index)
    a, b = a.loc[cohort], b.loc[cohort]
    rows = []
    for pid in cohort[a[flag].to_numpy(bool)]:
        y = float(a.at[pid, "actual"])
        d_abs = abs(float(a.at[pid, "xpts"]) - y) - abs(float(b.at[pid, "xpts"]) - y)
        d_brier = None
        pa, pb = a.at[pid, "p_start"], b.at[pid, "p_start"]
        if int(a.at[pid, "fixtures"]) == 1 and pa is not None and pb is not None \
                and not pd.isna(pa) and not pd.isna(pb):  # fmt: skip
            s = float(a.at[pid, "started"])
            d_brier = (float(pa) - s) ** 2 - (float(pb) - s) ** 2
        rows.append({"player_id": int(pid), "d_abs": d_abs, "d_brier": d_brier})
    briers = [r["d_brier"] for r in rows if r["d_brier"] is not None]
    return {"cohort": int(len(cohort)), "affected": rows,
            "mean_abs_error_change": float(np.mean([r["d_abs"] for r in rows])) if rows else None,
            "start_brier_change": float(np.mean(briers)) if briers else None,
            "brier_n": len(briers)}  # fmt: skip


def evaluate_gw(db: FootballDB, season: int, gw: int) -> dict[str, Any]:
    fin = pit.finality(db, season, gw)
    if not fin.final:
        return {"gw": gw, "final": False, "reason": fin.reason}
    prim = pit.eligible_run(db, season, gw, "primary")
    if not prim.found:
        return {"gw": gw, "final": True, "available": False, "reason": prim.reason}
    act = actuals(db, season, gw)
    raw = run_frame(db, prim.run_id, season, gw).merge(act, on="player_id")
    out: dict[str, Any] = {"gw": gw, "final": True, "available": True,
                           "primary_run": prim.run_id, "raw": metrics(raw), "comparisons": {},
                           "arms": {}, "excluded": []}  # fmt: skip
    rows = [{"variant": "raw", "run_id": prim.run_id, "m": out["raw"]}]
    prow = None if prim.legacy else _run_row(db, prim.run_id)
    if prow is None:
        out["issued"] = {"available": False, "reason": "legacy run: no issued record"}
        out["comparisons"] = {f: {"valid": False, "role": None, "reason": "legacy run"}
                              for f in overlays.FEATURES}  # fmt: skip
        return _store_evaluation(db, season, gw, out, rows)
    dl = pit.deadline(db, season, gw).at
    issued, arms = None, {}
    for child in _children(db, prim.run_id):
        role = child["flags"].get("role") or child.get("variant")
        why = check_arm(child, prow, season, gw, dl, matched=child.get("variant") == "experiment")
        if why:
            out["excluded"].append({"run_id": child["run_id"], "role": role, "reason": why})
            continue
        if child.get("variant") == "issued":
            issued = child
        elif child.get("variant") == "experiment":
            frame = run_frame(db, child["run_id"], season, gw).merge(act, on="player_id")
            arms[role] = (child["run_id"], frame, set(child["flags"].get("features_on") or []))
    if issued:
        f = run_frame(db, issued["run_id"], season, gw).merge(act, on="player_id")
        out["issued"] = {"run_id": issued["run_id"],
                         "transformations": issued["flags"].get("transformations"),
                         **{k: v for k, v in metrics(f).items()
                            if k in ("n", "mae", "rmse", "bias")}}  # fmt: skip
        rows.append({"variant": "issued", "run_id": issued["run_id"], "m": out["issued"]})
    else:
        out["issued"] = {"available": False, "reason": "no valid issued record for this run"}
    modes = prow["flags"].get("modes") or {}
    for feat in overlays.FEATURES:
        mode = modes.get(feat, "shadow")
        if mode == "off":
            out["comparisons"][feat] = {"valid": False, "role": None, "reason": "off"}
            continue
        role = "with" if mode == "shadow" else "without"
        a_role, b_role = (f"with:{feat}", "control") if mode == "shadow" else (
            "control", f"without:{feat}")  # fmt: skip
        a, b = arms.get(a_role), arms.get(b_role)
        if not a or not b:
            miss = [r for r, x in ((a_role, a), (b_role, b)) if not x]
            out["comparisons"][feat] = {"valid": False, "role": role,
                                        "reason": f"missing or invalid arm(s): {miss}"}  # fmt: skip
            continue
        if a[2] - b[2] != {feat} or not b[2] <= a[2]:
            out["comparisons"][feat] = {
                "valid": False,
                "role": role,
                "reason": "arms differ in more than this feature",
            }
            continue
        out["comparisons"][feat] = {"valid": True, "role": role, "with": a[0], "without": b[0],
                                    **_compare(a[1], b[1], feat)}  # fmt: skip
    if arms:
        common = set.intersection(*(set(f["player_id"]) for _, f, _ in arms.values()))
        for role, (run_id, f, _) in sorted(arms.items()):
            g = f[f["player_id"].isin(common)]
            m = metrics(g)
            for feat in overlays.FEATURES:
                m[f"{feat}_coverage"] = round(float(g[feat].mean()), 4) if len(g) else 0.0
            out["arms"][role] = {"run_id": run_id, **m}
            rows.append({"variant": role, "run_id": run_id, "m": m})
    return _store_evaluation(db, season, gw, out, rows)


def _store_evaluation(db: FootballDB, season: int, gw: int, out: dict, rows: list) -> dict:
    db.con.execute("DELETE FROM evaluations WHERE season = ? AND gw = ?", [season, gw])
    db.upsert(
        "evaluations",
        pd.DataFrame(
            [
                {
                    "season": season,
                    "gw": gw,
                    "variant": r["variant"],
                    "run_id": r["run_id"],
                    "cohort": "identical",
                    "n": int(r["m"].get("n") or 0),
                    "metrics": json.dumps(r["m"], default=str),
                    "created_at": now(),
                }
                for r in rows
            ]
        ),
    )
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
    d["config"] = json.loads(d["config"]) if d.get("config") else default_config()
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


def _feature_evidence(db: FootballDB, season: int, through_gw: int, mode: str, feature: str,
                      since_gw: int | None = None) -> dict[str, Any]:  # fmt: skip
    """Pooled paired evidence for one feature in its current mode: the comparisons of the right
    role (`with` while shadow: does adding it help; `without` while on: does removing it hurt
    less), from valid weeks after `since_gw` (when the mode last changed)."""
    rows = db.query("SELECT gw, payload FROM reviews WHERE season = ? AND kind = 'evaluation' "
                    "AND gw <= ? AND gw > ? ORDER BY gw",
                    [season, through_gw, since_gw or 0])  # fmt: skip
    role = "with" if mode == "shadow" else "without"
    diffs, briers, weeks, invalid = [], [], set(), []
    by_week: dict[int, list[float]] = {}
    for gw, blob in zip(rows["gw"], rows["payload"], strict=True):
        c = (json.loads(blob).get("comparisons") or {}).get(feature) or {}
        if not c.get("valid"):
            invalid.append({"gw": int(gw), "reason": c.get("reason", "no comparison")})
            continue
        if c.get("role") != role or not c.get("affected"):
            continue
        weeks.add(int(gw))
        d = [float(x["d_abs"]) for x in c["affected"]]
        diffs += d
        by_week.setdefault(int(gw), []).extend(d)
        briers += [float(x["d_brier"]) for x in c["affected"] if x.get("d_brier") is not None]
    n = len(diffs)
    # players in one gameweek share its fixtures and news: the gameweek is the independent unit
    wk = [float(np.mean(v)) for _, v in sorted(by_week.items())]
    return {
        "mode": mode,
        "role": role,
        "since_gw": since_gw,
        "gameweeks": len(weeks),
        "affected": n,
        "mean_abs_error_change": float(np.mean(diffs)) if n else None,
        "se": float(np.std(diffs, ddof=1) / np.sqrt(n)) if n > 1 else None,
        "start_brier_change": float(np.mean(briers)) if briers else None,
        "brier_n": len(briers),
        "invalid_weeks": invalid,
        "per_week": [round(x, 4) for x in wk],
        "block_mean": float(np.mean(wk)) if wk else None,
        "block_se": float(np.std(wk, ddof=1) / np.sqrt(len(wk))) if len(wk) > 1 else None,
    }


def decide_feature(ev: dict, mode: str) -> tuple[str, str]:
    """(new mode, reason) under the fixed THRESHOLDS. Missing metrics never change a mode."""
    t = THRESHOLDS
    if mode == "off":
        return "off", "off by configuration"
    enough = ev["gameweeks"] >= t["min_gameweeks"] and ev["affected"] >= t["min_affected"]
    if not enough or ev["mean_abs_error_change"] is None or ev["se"] is None:
        return mode, (
            f"insufficient evidence ({ev['gameweeks']} of {t['min_gameweeks']} "
            f"gameweeks, {ev['affected']} of {t['min_affected']} player-weeks)"
        )
    m, se, k = ev["mean_abs_error_change"], ev["se"], t["se_multiple"]
    if mode == "shadow":
        if ev["start_brier_change"] is None:
            return mode, "no P(start) Brier evidence on the affected players: not promoted"
        bm, bse = ev.get("block_mean"), ev.get("block_se")
        if m + k * se < 0 and ev["start_brier_change"] <= t["max_brier_regression"]:
            if bse is None or bm + k * bse >= 0:  # must also hold across whole gameweeks
                return mode, (
                    f"not promoted: player-week abs error {m:+.3f} +- {se:.3f} but the "
                    "gameweek-level change "
                    + ("is unavailable" if bse is None else f"{bm:+.3f} +- {bse:.3f}")
                    + f" is not below zero by {k:g} SE"
                )
            return "on", (f"promoted: abs error {m:+.3f} +- {se:.3f} per affected player-week, "
                          f"{bm:+.3f} +- {bse:.3f} per gameweek, Brier "
                          f"{ev['start_brier_change']:+.4f}")  # fmt: skip
    if mode == "on" and m - k * se > 0:
        return "shadow", f"demoted: abs error {m:+.3f} +- {se:.3f} per affected player-week"
    return mode, f"no significant change: abs error {m:+.3f} +- {se:.3f}"


def _holdout_mae(db: FootballDB, season: int, gw: int, factors: dict[str, float]) -> float | None:
    prim = pit.eligible_run(db, season, gw)
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
    # not_final | unchanged | promoted | retained | rejected | corrected | correction_recorded
    # | out_of_order | rolled_back
    status: str
    version: int | None
    reason: str
    evaluation: dict = field(default_factory=dict)

    def text(self) -> str:
        return f"learning: {self.status} (version {self.version}): {self.reason}"


def evidence_key(db: FootballDB, season: int, gw: int) -> str:
    """A digest of everything an update for (season, gw) reads: the stored evaluations and final
    reviews up to `gw`, the gameweek's official results, the versions and the thresholds."""
    ev = db.query("SELECT gw, payload FROM reviews WHERE season = ? AND kind = 'evaluation' AND "
                  "gw <= ? ORDER BY gw", [season, gw])  # fmt: skip
    rv = db.query("SELECT gw, payload FROM reviews WHERE season = ? AND kind = 'prediction' AND "
                  "gw <= ? ORDER BY gw", [season, gw])  # fmt: skip
    act = actuals(db, season, gw).sort_values("player_id")
    blob = json.dumps({
        "season": season, "gw": gw, "learning": LEARNING_VERSION, "model": pit.MODEL_VERSION,
        "features": pit.FEATURE_VERSION, "thresholds": THRESHOLDS, "step": MAX_CAL_STEP,
        "evaluations": list(zip(ev["gw"].tolist(), ev["payload"].tolist(), strict=True)),
        "reviews": [(g, p) for g, p in zip(rv["gw"].tolist(), rv["payload"].tolist(), strict=True)
                    if calibration.usable(json.loads(p))],
        "actuals": act[["player_id", "actual", "minutes", "fixtures"]].astype(float).values
        .round(6).tolist(),
    }, sort_keys=True, default=str)  # fmt: skip
    return hashlib.sha256(blob.encode()).hexdigest()[:32]


def effective_calibration(db: FootballDB, season: int, gw: int,
                          version: int | None) -> dict[str, float]:  # fmt: skip
    """The calibration a run for `gw` uses under learning-state `version`: the state's factors,
    or with no state the factors from final reviews before `gw` (as `agent.active_config`)."""
    if version is None:
        return calibration.factors(db, season, gw)
    return dict(_config_of(db, version).get("calibration") or {})


def _config_of(db: FootballDB, version: int | None) -> dict:
    if version is None:
        return default_config()
    r = db.query("SELECT config FROM learning_state WHERE version = ?", [version])
    cfg = json.loads(r["config"].iloc[0]) if len(r) else default_config()
    cfg.setdefault("since", {})
    return cfg


def _record_update(db: FootballDB, season: int, gw: int, key: str, status: str,
                   base: int | None, result: int | None, reason: str) -> None:  # fmt: skip
    db.upsert("learning_updates", pd.DataFrame([{
        "season": season, "gw": gw, "evidence_key": key, "status": status, "base_version": base,
        "result_version": result, "created_at": now(), "reason": reason[:500]}]).astype(
            {"base_version": "Int64", "result_version": "Int64"}))  # fmt: skip


def weekly_update(db: FootballDB, season: int, gw: int) -> UpdateResult:
    fin = pit.finality(db, season, gw)
    if not fin.final:
        return UpdateResult("not_final", None, f"GW{gw} is provisional: {fin.reason}")
    ev = evaluate_gw(db, season, gw)
    key = evidence_key(db, season, gw)
    cur = current_state(db)
    cur_v = int(cur["version"]) if cur else None
    done = db.query("SELECT * FROM learning_updates WHERE season = ? ORDER BY created_at",
                    [season])  # fmt: skip
    mine = done[done["gw"] == gw]
    # a retry of the evidence this gameweek was last processed with changes nothing; a return to
    # evidence seen earlier (A, then a correction B, then A again) is a correction like any other
    if len(mine) and mine.iloc[-1]["evidence_key"] == key:
        return UpdateResult("unchanged", cur_v, f"GW{gw} already processed with identical "
                            f"evidence ({key[:8]}): nothing changes", ev)  # fmt: skip
    later = done[done["gw"] > gw]
    if len(later):
        why = (f"GW{gw} evidence is new but GW{int(later['gw'].max())} was processed after it: "
               "recorded, state unchanged (it is read by the next update)")  # fmt: skip
        _record_update(db, season, gw, key, "out_of_order", cur_v, cur_v, why)
        return UpdateResult("out_of_order", cur_v, why, ev)
    correction = bool(len(mine))
    if correction:
        last = mine.iloc[-1]
        produced = None if pd.isna(last["result_version"]) else int(last["result_version"])
        if produced != cur_v:
            why = (f"GW{gw} evidence changed since its update, but the state changed after it "
                   f"(now version {cur_v}): correction recorded, state unchanged")  # fmt: skip
            _record_update(db, season, gw, key, "correction_recorded", cur_v, cur_v, why)
            return UpdateResult("correction_recorded", cur_v, why, ev)
        base_v = None if pd.isna(last["base_version"]) else int(last["base_version"])
    else:
        base_v = cur_v
    base = _config_of(db, base_v)
    # the calibration in force, resolved as the agent resolves it (`agent.active_config`): the
    # state's own factors, or without a state the factors from final reviews. `base_cal` is what
    # the next run would use; `hold_cal` is what the holdout week's forecast was issued with.
    base_cal = effective_calibration(db, season, gw + 1, base_v)
    prow = _run_row(db, ev["primary_run"]) if ev.get("primary_run") else None
    recorded = ((prow or {}).get("flags") or {}).get("config", {}).get("calibration")
    hold_cal = dict(recorded) if isinstance(recorded, dict) else effective_calibration(
        db, season, gw, base_v)  # fmt: skip
    since = dict(base.get("since") or {})
    # calibration: validate the procedure on the holdout week (estimated without it), then refit
    n_reviews = sum(
        1 for b in db.query("SELECT payload FROM reviews WHERE season = ? AND kind = 'prediction' "
                            "AND gw <= ?", [season, gw])["payload"]
        if calibration.usable(json.loads(b)))  # fmt: skip
    val_fac = _bounded(hold_cal, calibration.factors(db, season, gw))
    new_fac = _bounded(base_cal, calibration.factors(db, season, gw + 1))
    if n_reviews < THRESHOLDS["min_calibration_weeks"]:
        new_fac, val_fac = dict(base_cal), dict(hold_cal)
    mae_cur = _holdout_mae(db, season, gw, hold_cal)
    mae_cand = _holdout_mae(db, season, gw, val_fac)
    reasons: dict[str, str] = {}
    if new_fac != base_cal and (mae_cur is None or mae_cand is None):
        new_fac = dict(base_cal)
        reasons["calibration"] = "no holdout validation available: calibration kept"
    feats, new_modes = {}, {}
    for f in overlays.FEATURES:
        mode = (base.get("features") or {}).get(f, "shadow")
        feats[f] = _feature_evidence(db, season, gw, mode, f, since.get(f))
        new_modes[f], reasons[f] = decide_feature(feats[f], mode)
        if new_modes[f] != mode:
            since[f] = gw
    cand = {"calibration": new_fac, "features": new_modes, "since": since}
    evaluation = {"holdout_gw": gw, "base_calibration": base_cal,
                  "holdout_calibration_current": hold_cal, "holdout_calibration_candidate": val_fac,
                  "holdout_mae_current": mae_cur,
                  "holdout_mae_candidate": mae_cand, "features": feats, "decisions": reasons,
                  "thresholds": THRESHOLDS, "evidence_key": key, "base_version": base_v,
                  "correction": correction, "weekly": ev}  # fmt: skip
    samples = {"final_reviews": n_reviews, **{f"{k}_affected": v["affected"]
                                              for k, v in feats.items()}}  # fmt: skip
    cutoff = pit.deadline(db, season, gw + 1).at
    reason = "; ".join(f"{k}: {v}" for k, v in reasons.items())
    cur_cfg = _config_of(db, cur_v)
    cur_cfg["calibration"] = effective_calibration(db, season, gw + 1, cur_v)
    if cand == cur_cfg:  # nothing to change (for a correction: it lands where the state is)
        status = "corrected" if correction else "retained"
        _record_update(db, season, gw, key, status, base_v, cur_v, reason)
        return UpdateResult(
            status,
            cur_v,
            ("correction leaves the state as it is: " if correction else "no change proposed: ")
            + reason,
            evaluation,
        )
    worse = (mae_cur is not None and mae_cand is not None
             and mae_cand > mae_cur + THRESHOLDS["max_regression"])  # fmt: skip
    if worse:
        why = f"holdout MAE {mae_cand:.3f} vs current {mae_cur:.3f}"
        v = _store_state(db, "rejected", base_v, season, gw, cutoff, cand, samples, evaluation,
                         why)  # fmt: skip
        _record_update(db, season, gw, key, "rejected", base_v, cur_v, why)
        return UpdateResult("rejected", v, "candidate worse on the holdout week; current kept",
                            evaluation)  # fmt: skip
    v = _store_state(db, "current", base_v, season, gw, cutoff, cand, samples, evaluation, reason)
    status = "corrected" if correction else "promoted"
    _record_update(db, season, gw, key, status, base_v, v, reason)
    return UpdateResult(status, v, f"candidate adopted (holdout MAE {mae_cand} vs {mae_cur}): "
                        + reason, evaluation)  # fmt: skip


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
WF_DEFAULT_ABLATIONS = {
    "all": ["xc_rest_days", "xc_mins_7d", "xc_mins_14d", "xc_team_matches_7d"],
    "rest_only": ["xc_rest_days"],
    "no_team_congestion": ["xc_rest_days", "xc_mins_7d", "xc_mins_14d"],
}


def pit_pool(db: FootballDB, season: int, gw: int, cutoff: datetime) -> pd.DataFrame:
    """Each player's newest squad snapshot observed at or before `cutoff` in the season (labelled
    for this gameweek or an earlier one). An archive backfill row is stamped at its gameweek's
    first kickoff, i.e. after that deadline, so for a backfilled gameweek this is the previous
    gameweek's row: strictly what was known, at the price of new signings being absent."""
    return db.query(
        "SELECT player_id, fpl_id, team_id, position, now_cost AS price, status, "
        "chance_next AS chance FROM fpl_players_gw WHERE season = ? AND gw <= ? "
        "AND snapshot_at <= ? AND player_id IS NOT NULL QUALIFY row_number() OVER "
        "(PARTITION BY player_id ORDER BY gw DESC, snapshot_at DESC) = 1",
        [season, gw, cutoff],
    )


def _row_deadlines(db: FootballDB, rows: pd.DataFrame) -> np.ndarray:
    """The deadline of each row's gameweek (FPL's, else first kickoff - 90 min)."""
    est = rows.groupby(["season", "gw"])["kickoff"].transform("min") - pit.DEADLINE_LEAD
    ev = db.query("SELECT season, gw, deadline_time FROM fpl_events")
    if not ev.empty:
        m = rows[["season", "gw"]].merge(ev, on=["season", "gw"], how="left")["deadline_time"]
        est = pd.Series(np.where(m.notna(), pd.to_datetime(m), est), index=rows.index)
    return pd.to_datetime(est).to_numpy("datetime64[ns]")


def _wf_models(db, season, gw, ablations, apps, teams) -> dict:
    """Models fitted only on what was known at the deadline of `gw`: outcome rows of earlier
    gameweeks, squad snapshots observed by then, workload facts public by each training row's
    own deadline."""
    from fpl_agent.model import features, minutes, workload

    cutoff = pit.deadline(db, season, gw).at
    frame = features.player_gw_frame(db, as_of=cutoff)
    hist = frame[(frame["season"] < season) | ((frame["season"] == season) & (frame["gw"] < gw))]
    if len(hist) < 500:
        return {}
    base = minutes.MinutesModel.fit(hist)
    feat = minutes.training_rows(hist)
    feat = feat[feat["kickoff"].notna()]
    wl = workload.workload_features(feat, apps, teams, cutoff=_row_deadlines(db, feat))
    models = {"base": base, "hist": hist, "fitted_for": gw}
    for name, cols in ablations.items():
        models[name] = _fit_subset(feat, wl, base.cols, cols)
    return models


def walk_forward_rows(
    db: FootballDB,
    season: int,
    gw: int,
    models: dict | None = None,
    ablations: dict | None = None,
    apps=None,
    teams=None,
) -> pd.DataFrame:
    """One row per (player, fixture) of `gw` with the baseline and each ablation's P(start),
    forecast the way the agent forecasts at the deadline: each player scored once from the
    history before it (a double gameweek's second fixture gets the same forecast, never one
    built from the first fixture's result), the squad snapshot observed by the deadline, and
    workload facts public by then. `started` is joined afterwards for scoring only."""
    from fpl_agent.model import workload
    from fpl_agent.model.features import minutes_features

    ablations = ablations or WF_DEFAULT_ABLATIONS
    apps = workload.appearance_log(db) if apps is None else apps
    teams = workload.team_log(db) if teams is None else teams
    models = models or _wf_models(db, season, gw, ablations, apps, teams)
    cutoff = pit.deadline(db, season, gw).at
    if not models or cutoff is None:
        return pd.DataFrame()
    base = models["base"]  # fitted estimators, reused between refits
    # the history that feeds this forecast's inputs is rebuilt at its own deadline, so results
    # final since the last refit count without one; squad rows observed after it never do
    if models.get("fitted_for") == gw:
        hist = models["hist"]
    else:
        from fpl_agent.model import features

        frame = features.player_gw_frame(db, as_of=cutoff)
        hist = frame[(frame["season"] < season)
                     | ((frame["season"] == season) & (frame["gw"] < gw))]  # fmt: skip
    pool = pit_pool(db, season, gw, cutoff)
    sched = db.query(
        "SELECT player_id, fixture_id, kickoff FROM fpl_points_gw WHERE season = ? "
        "AND gw = ? AND player_id IS NOT NULL AND kickoff IS NOT NULL",
        [season, gw],
    )  # fmt: skip  (the fixture list only: no outcome columns)
    up = sched.merge(pool, on="player_id", how="inner")
    if up.empty:
        return pd.DataFrame()
    up = up.sort_values(["player_id", "kickoff", "fixture_id"]).reset_index(drop=True)
    first = up.groupby("player_id", as_index=False).first()
    stub = first.assign(season=season, gw=gw, minutes=0, started=False, fixture_id=-1)
    known = hist[hist["player_id"].isin(stub["player_id"])]
    both = pd.concat([known.assign(_new=False), stub.assign(_new=True)], ignore_index=True)
    both = both.sort_values(["player_id", "_new", "season", "gw"], kind="stable")
    feat = minutes_features(both.drop(columns=["_new"]))[both["_new"].to_numpy()]
    feat = feat.reset_index(drop=True)
    wl = workload.workload_features(feat, apps, teams, cutoff=cutoff)
    pred = pd.DataFrame({"player_id": feat["player_id"].to_numpy(),
                         "p_baseline": base.predict(feat)["p_start"].to_numpy(),
                         "known": wl.notna().any(axis=1).to_numpy()})  # fmt: skip
    for name in ablations:
        pred[f"p_{name}"] = models[name].predict(feat[base.cols], wl)
    pred = pd.concat([pred, wl.reset_index(drop=True)], axis=1)  # the workload inputs used
    out = up[["player_id", "fixture_id", "kickoff"]].merge(pred, on="player_id", how="left")
    out["cutoff"] = pd.Timestamp(cutoff)
    out["snapshot_players"] = len(pool)
    res = db.query("SELECT player_id, fixture_id, minutes, TRY_CAST(json_extract_string(stats, "
                   "'$.starts') AS DOUBLE) AS starts FROM fpl_points_gw WHERE season = ? AND "
                   "gw = ?", [season, gw])  # fmt: skip
    res["started"] = np.where(res["starts"].notna(), res["starts"] > 0, res["minutes"] >= 60)
    return out.merge(res[["player_id", "fixture_id", "started"]], on=["player_id", "fixture_id"],
                     how="inner")  # fmt: skip


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

    Every test forecast comes from `walk_forward_rows`: models fitted on what was known at the
    deadline of the gameweek they were (re)fitted for, which is never later than the gameweek
    they forecast; squad rows and workload facts as known at that gameweek's deadline. The
    first `tune_share` of gameweeks chooses the bound; the remaining gameweeks are the final
    test, scored once with that bound and never used to choose anything. Ablations refit the
    workload model with subsets of the workload features.
    """
    from fpl_agent.model import workload

    ablations = ablations or WF_DEFAULT_ABLATIONS
    apps, teams = workload.appearance_log(db), workload.team_log(db)
    per_gw, models = [], {}
    for gw in gws:
        if not models or gw - models["fitted_for"] >= refit_every:
            models = _wf_models(db, season, gw, ablations, apps, teams) or models
        rows = walk_forward_rows(db, season, gw, models, ablations, apps, teams) if models else (
            pd.DataFrame())  # fmt: skip
        if rows.empty:
            continue
        known = rows["known"].to_numpy(bool)
        y = rows["started"].to_numpy(float)
        pb = rows["p_baseline"].to_numpy(float)
        rec = {"gw": gw, "n": int(len(rows)), "known": int(known.sum()),
               "snapshot_players": int(rows["snapshot_players"].iloc[0]),
               "baseline": _scores(pb, y)}  # fmt: skip
        for name in ablations:
            pe = rows[f"p_{name}"].to_numpy(float)
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
        "test_by_gw": [{"gw": r["gw"], "n": r["n"],
                        **{name: round((r[a]["brier_sum"] - r["baseline"]["brier_sum"]) / r["n"],
                                       6) for name, a in best.items()}} for r in test_weeks],
        "method": "deadline forecasts (walk_forward_rows); refit every "
                  f"{refit_every} gameweeks on data known at the refit deadline",
        "limitations": [
            "minutes-level only (P(start) Brier and log loss); points need `backtest`",
            "domestic cup matches are not in the database, so their workload is invisible",
            "FPL archive seasons carry no availability flags: rows of injured players are"
            + " included, which inflates every arm's error equally",
            "archive squad rows are stamped at kickoff, after the deadline: each gameweek uses"
            + " the newest row observed by its deadline (usually the previous gameweek's), so"
            + " players new to the squad that week are not forecast",
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
