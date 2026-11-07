"""Model performance from forecasts that were actually issued before each deadline.

For every gameweek whose results are final, `pit.eligible_run` picks the one forecast run made
before that gameweek's deadline (a registered run, or a legacy run stored before the registry
existed). It is scored against FPL's official points on one cohort per gameweek: players who
have the model's forecast, FPL's own pre-deadline expected points (`ep_next` in the squad
snapshot taken before the deadline) and a recent-form baseline (average points over the
previous five gameweeks). No forecast is re-made after the fact.

Uncertainty is measured across gameweeks (each gameweek is one block), not across player rows,
because rows within a gameweek share fixtures and are not independent.

Historical replays are reported separately and always labelled as backtests.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from fpl_agent import learning, pit
from fpl_agent.db.database import FootballDB

XP_BINS = [-10, 1, 2, 3, 4, 6, 100]
XP_LABELS = ["<1", "1-2", "2-3", "3-4", "4-6", "6+"]


def _ep_next(db: FootballDB, season: int, gw: int, deadline) -> pd.DataFrame:
    """FPL's expected points for `gw` as published in the last snapshot before its deadline."""
    df = db.query(
        "SELECT player_id, raw FROM fpl_players_gw WHERE season = ? AND gw = ? AND "
        "snapshot_at <= ? QUALIFY snapshot_at = max(snapshot_at) OVER (PARTITION BY fpl_id)",
        [season, gw, deadline],
    )
    vals = []
    for r in df.itertuples():
        try:
            v = json.loads(r.raw).get("ep_next")
            vals.append((int(r.player_id), float(v) if v not in (None, "") else None))
        except (TypeError, ValueError):
            vals.append((int(r.player_id), None))
    out = pd.DataFrame(vals, columns=["player_id", "ep_next"]).dropna()
    return out.drop_duplicates("player_id")


def _form(db: FootballDB, season: int, gw: int, n: int = 5) -> pd.DataFrame:
    return db.query(
        "SELECT player_id, avg(pts) AS form5 FROM (SELECT player_id, gw, sum(total_points) AS pts "
        "FROM fpl_points_gw WHERE season = ? AND gw < ? AND gw >= ? AND player_id IS NOT NULL "
        "GROUP BY player_id, gw) GROUP BY player_id",
        [season, gw, gw - n],
    )


def _err(df: pd.DataFrame, col: str) -> dict[str, float]:
    e = df[col] - df["actual"]
    return {
        "mae": float(e.abs().mean()),
        "rmse": float(np.sqrt((e**2).mean())),
        "bias": float(e.mean()),
    }


def cohort(db: FootballDB, season: int, gw: int):
    """(base, run, frame): the scored player-weeks of a final gameweek: players with the
    issued pre-deadline forecast, FPL's pre-deadline expected points, five gameweeks of history
    and the final result. `frame` is None when the gameweek cannot be scored."""
    fin = pit.finality(db, season, gw)
    run = pit.eligible_run(db, season, gw)
    base = {"gw": gw, "final": fin.final, "finality": fin.reason}
    if not fin.final:
        return {**base, "status": "not final"}, run, None
    if not run.found:
        return {**base, "status": "no eligible forecast", "reason": run.reason}, run, None
    f = learning.run_frame(db, run.run_id, season, gw).merge(
        learning.actuals(db, season, gw), on="player_id"
    )
    f = f.merge(_ep_next(db, season, gw, run.deadline), on="player_id").merge(
        _form(db, season, gw), on="player_id"
    )
    return base, run, f


def gameweek(db: FootballDB, season: int, gw: int) -> dict[str, Any] | None:
    base, run, f = cohort(db, season, gw)
    if f is None:
        return base
    if f.empty:
        return {**base, "status": "empty cohort"}
    m = learning.metrics(f)
    return {
        **base,
        "status": "scored",
        "run_id": run.run_id,
        "issued_at": run.created_at.isoformat() if run.created_at else None,
        "deadline": run.deadline.isoformat() if run.deadline else None,
        "deadline_source": run.deadline_source,
        "legacy_run": run.legacy,
        "n": int(len(f)),
        "model": _err(f, "xpts"),
        "fpl_ep": _err(f, "ep_next"),
        "form5": _err(f, "form5"),
        "start_brier": m.get("start_brier"),
        "start_calibration": m.get("start_calibration"),
        "rows": f[["xpts", "ep_next", "form5", "actual"]].astype(float).to_numpy().tolist(),
    }


def summary(db: FootballDB, season: int, gws: list[int] | None = None) -> dict[str, Any]:
    if gws is None:
        gws = [
            r[0]
            for r in db.con.execute(
                "SELECT DISTINCT gw FROM fpl_points_gw WHERE season = ? ORDER BY gw", [season]
            ).fetchall()
        ]
    weeks = [w for w in (gameweek(db, season, g) for g in gws) if w]
    scored = [w for w in weeks if w["status"] == "scored"]
    out: dict[str, Any] = {
        "season": season,
        "kind": "forward",
        "label": "Forecasts issued before each deadline, scored after the gameweek was final",
        "weeks": [{k: v for k, v in w.items() if k != "rows"} for w in weeks],
        "gameweeks_scored": len(scored),
        "player_weeks": int(sum(w["n"] for w in scored)),
    }
    if not scored:
        out["verdict"] = "No eligible issued forecasts with final results yet."
        return out
    rows = np.array([r for w in scored for r in w["rows"]])
    pred, ep, form, act = rows.T
    calib = []
    bins = pd.cut(pred, XP_BINS, labels=XP_LABELS)
    for lab in XP_LABELS:
        sel = np.asarray(bins == lab)
        if sel.sum():
            calib.append(
                {
                    "bin": lab,
                    "n": int(sel.sum()),
                    "pred": round(float(pred[sel].mean()), 2),
                    "actual": round(float(act[sel].mean()), 2),
                }
            )
    out["pooled"] = {
        "model": {
            k: round(v, 3)
            for k, v in _err(pd.DataFrame({"xpts": pred, "actual": act}), "xpts").items()
        },
        "fpl_ep": {
            k: round(v, 3)
            for k, v in _err(pd.DataFrame({"ep_next": ep, "actual": act}), "ep_next").items()
        },
        "form5": {
            k: round(v, 3)
            for k, v in _err(pd.DataFrame({"form5": form, "actual": act}), "form5").items()
        },
    }
    out["calibration"] = calib
    for base in ("fpl_ep", "form5"):  # per-gameweek MAE difference: one block per week
        d = np.array([w["model"]["mae"] - w[base]["mae"] for w in scored])
        se = float(d.std(ddof=1) / np.sqrt(len(d))) if len(d) > 1 else None
        out[f"vs_{base}"] = {
            "mean_mae_difference": round(float(d.mean()), 3),
            "se": None if se is None else round(se, 3),
            "gameweeks": len(d),
            "per_week": [round(float(x), 3) for x in d],
            "verdict": _verdict(float(d.mean()), se, len(d)),
        }
    return out


def _verdict(mean: float, se: float | None, n: int) -> str:
    if n < 4 or se is None:
        return (
            f"insufficient evidence: {n} gameweek(s) scored; at least 4 are needed before "
            "any comparison is stated"
        )
    if mean + 2 * se < 0:
        return "model error lower than the baseline by more than two standard errors"
    if mean - 2 * se > 0:
        return "model error higher than the baseline by more than two standard errors"
    return "no difference established (within two standard errors)"


def backtests(eval_dir: Path) -> list[dict]:
    """Stored historical replays (challenger evaluations); each is labelled a backtest."""
    out = []
    for p in sorted(Path(eval_dir).glob("*.json")):
        try:
            d = json.loads(p.read_text())
        except ValueError:
            continue
        d["kind"] = "backtest"
        out.append(d)
    return out


__all__ = ["summary", "gameweek", "cohort", "backtests"]
