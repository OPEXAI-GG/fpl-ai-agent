"""L8 review loop: after a gameweek, how wrong were the projections (and where), and how good
were the decisions compared with simple alternatives and with hindsight?

Reports are written to the `reviews` table and returned. The loop *reports and flags*; it never
changes a model or a rule by itself. A flag ("MID bias +0.4") is what a human or a scheduled
refit acts on, which keeps the live agent from chasing noise.
"""

from __future__ import annotations

import json
from typing import Any

import numpy as np
import pandas as pd

from fpl_agent.db.database import FootballDB, now
from fpl_agent.solver.decide import Decision

COMPONENT_ALIAS = {"goals": "goals_scored", "saves": "saves"}
BIAS_FLAG = 0.35  # points per player-gameweek


def decision_payload(d: Decision) -> dict[str, Any]:
    return {
        "gw": d.gw, "action": d.action, "chip": d.chip, "transfers_in": d.transfers_in,
        "transfers_out": d.transfers_out, "hits": d.hits, "captain": d.captain, "vice": d.vice,
        "xi": d.xi, "bench": d.bench, "xp": round(d.xp, 3),
        "xp_no_transfers": round(d.xp_no_transfers, 3), "bank_after": d.bank_after,
        "held": d.chip_report.get("held", {}), "warnings": d.warnings,
    }  # fmt: skip


def save_decision(db: FootballDB, season: int, d: Decision, mode: str) -> None:
    db.append("decisions", pd.DataFrame([{
        "season": season, "gw": d.gw, "created_at": now(), "mode": mode,
        "payload": json.dumps(decision_payload(d)),
    }]))  # fmt: skip


def _actuals(db: FootballDB, season: int, gw: int) -> pd.DataFrame:
    return db.query(
        "SELECT player_id, sum(total_points) AS actual, sum(minutes) AS minutes, "
        "any_value(explain) AS explain FROM fpl_points_gw "
        "WHERE season = ? AND gw = ? AND player_id IS NOT NULL GROUP BY player_id",
        [season, gw],
    )


def _explain_components(blob: str | None) -> dict[str, float]:
    out: dict[str, float] = {}
    for item in json.loads(blob or "[]"):
        out[item["identifier"]] = out.get(item["identifier"], 0.0) + float(item["points"])
    return out


def prediction_review(db: FootballDB, season: int, gw: int) -> dict[str, Any]:
    proj = db.query(
        "SELECT player_id, xpts, detail FROM projections WHERE season = ? AND gw = ? "
        "QUALIFY created_at = max(created_at) OVER (PARTITION BY player_id)",
        [season, gw],
    )
    act = _actuals(db, season, gw)
    if proj.empty or act.empty:
        return {"gw": gw, "available": False}
    pos = db.query("SELECT player_id, position FROM players")
    df = proj.merge(act, on="player_id", how="left").merge(pos, on="player_id", how="left")
    df["actual"] = df["actual"].fillna(0.0)
    df["err"] = df["xpts"] - df["actual"]
    by_pos = {}
    for p, g in df.groupby("position"):
        by_pos[p] = {"n": len(g), "bias": round(float(g["err"].mean()), 3),
                     "rmse": round(float(np.sqrt((g["err"] ** 2).mean())), 3),
                     "mae": round(float(g["err"].abs().mean()), 3)}  # fmt: skip
    comp: dict[str, list[float]] = {}
    for r in df.itertuples():
        pred = (json.loads(r.detail) if r.detail else {}).get("components", {})
        real = _explain_components(r.explain if isinstance(r.explain, str) else None)
        for name, v in pred.items():
            comp.setdefault(COMPONENT_ALIAS.get(name, name), [0.0, 0.0, 0.0])
            c = comp[COMPONENT_ALIAS.get(name, name)]
            c[0] += v
            c[1] += real.get(COMPONENT_ALIAS.get(name, name), 0.0)
            c[2] += 1
    components = {k: {"predicted": round(v[0] / v[2], 4), "actual": round(v[1] / v[2], 4)}
                  for k, v in comp.items() if v[2]}  # fmt: skip
    flags = [
        f"{p} over/under-projected by {m['bias']:+.2f} pts per player"
        for p, m in by_pos.items()
        if abs(m["bias"]) > BIAS_FLAG and m["n"] >= 30
    ]
    # top-end calibration: did the players we rated highest deliver?
    top = df.nlargest(20, "xpts")
    report = {"gw": gw, "available": True, "n": len(df),
              "rmse": round(float(np.sqrt((df["err"] ** 2).mean())), 3),
              "bias": round(float(df["err"].mean()), 3), "by_position": by_pos,
              "components": components, "flags": flags,
              "top20": {"predicted": round(float(top["xpts"].mean()), 2),
                        "actual": round(float(top["actual"].mean()), 2)}}  # fmt: skip
    _store(db, season, gw, "prediction", report)
    return report


def decision_review(db: FootballDB, season: int, gw: int) -> dict[str, Any]:
    row = db.con.execute(
        "SELECT payload FROM decisions WHERE season = ? AND gw = ? "
        "ORDER BY created_at DESC LIMIT 1",
        [season, gw],
    ).fetchone()
    act = _actuals(db, season, gw).set_index("player_id")
    if not row or act.empty:
        return {"gw": gw, "available": False}
    d = json.loads(row[0])
    pts = act["actual"].to_dict()
    got = lambda ids: [float(pts.get(i, 0.0)) for i in ids]  # noqa: E731
    xi_pts = sum(got(d["xi"])) + float(pts.get(d["captain"], 0.0))
    best_cap = max(got(d["xi"]), default=0.0)
    gain_in = sum(got(d["transfers_in"])) - sum(got(d["transfers_out"]))
    report = {
        "gw": gw, "available": True, "action": d["action"], "xi_points": xi_pts,
        "hits": d["hits"], "points_after_hits": xi_pts - 4 * d["hits"],
        "captain_points": float(pts.get(d["captain"], 0.0)), "best_possible_captain": best_cap,
        "captain_regret": best_cap - float(pts.get(d["captain"], 0.0)),
        "transfer_gain": gain_in, "transfer_gain_after_hits": gain_in - 4 * d["hits"],
        "predicted": d["xp"], "predicted_vs_actual": round(d["xp"] - xi_pts, 2),
    }  # fmt: skip
    _store(db, season, gw, "decision", report)
    return report


def _store(db: FootballDB, season: int, gw: int, kind: str, payload: dict) -> None:
    db.con.execute(
        "DELETE FROM reviews WHERE season = ? AND gw = ? AND kind = ?", [season, gw, kind]
    )
    row = {"season": season, "gw": gw, "created_at": now(), "kind": kind}
    db.append("reviews", pd.DataFrame([{**row, "payload": json.dumps(payload)}]))


def summarize(db: FootballDB, season: int) -> dict[str, Any]:
    """Running totals across reviewed gameweeks, for the weekly report."""
    rows = db.query("SELECT gw, kind, payload FROM reviews WHERE season = ? ORDER BY gw", [season])
    pred = [
        json.loads(p)
        for k, p in zip(rows["kind"], rows["payload"], strict=True)
        if k == "prediction"
    ]
    dec = [
        json.loads(p) for k, p in zip(rows["kind"], rows["payload"], strict=True) if k == "decision"
    ]
    return {
        "weeks_reviewed": len(pred),
        "mean_rmse": round(float(np.mean([p["rmse"] for p in pred])), 3) if pred else None,
        "mean_bias": round(float(np.mean([p["bias"] for p in pred])), 3) if pred else None,
        "total_points": sum(d["points_after_hits"] for d in dec),
        "captain_regret": (
            round(float(np.mean([d["captain_regret"] for d in dec])), 2) if dec else None
        ),
    }
