"""L8 review loop: after a gameweek, how wrong were the projections (and where), and how good
were the decisions compared with simple alternatives and with hindsight?

Reports are written to the `reviews` table and returned. The loop *reports and flags*; it never
changes a model or a rule by itself.

Only one forecast is reviewed per gameweek: the newest whole run made before FPL's deadline
(`pit.eligible_run`), so a run made after kickoff can never produce a falsely perfect review and
runs are never mixed per player. Each report says which run it scored, whether the gameweek's
results are final, and how many forecast players have no official result yet (they are left out,
never scored as zero). Calibration and lessons use final reports only.

A flag ("MID bias +0.4") is what a human or a scheduled refit acts on, which keeps the live agent
from chasing noise.
"""

from __future__ import annotations

import json
import logging
from typing import Any

import numpy as np
import pandas as pd

from fpl_agent import pit
from fpl_agent.db.database import FootballDB, now
from fpl_agent.lessons import TEMPLATE
from fpl_agent.solver.decide import Decision

log = logging.getLogger(__name__)
COMPONENT_ALIAS = {"goals": "goals_scored", "saves": "saves"}
BIAS_FLAG = 0.35  # points per player-gameweek


def decision_payload(d: Decision) -> dict[str, Any]:
    return {
        "gw": d.gw, "action": d.action, "chip": d.chip, "transfers_in": d.transfers_in,
        "transfers_out": d.transfers_out, "hits": d.hits, "captain": d.captain, "vice": d.vice,
        "xi": d.xi, "bench": d.bench, "xp": round(d.xp, 3),
        "xp_no_transfers": round(d.xp_no_transfers, 3), "bank_after": d.bank_after,
        "held": d.chip_report.get("held", {}), "warnings": d.warnings,
        "captain_options": [{"player_id": int(o["player_id"]), "xpts": round(o["xpts"], 2)}
                            for o in d.captain_options],
        # the moves planned for the next weeks, so the lessons can see what was held
        "plan": [{"gw": w.gw, "in": w.transfers_in, "out": w.transfers_out}
                 for w in d.horizon[1:3]],
    }  # fmt: skip


def save_decision(db: FootballDB, season: int, d: Decision, mode: str) -> None:
    db.append("decisions", pd.DataFrame([{
        "season": season, "gw": d.gw, "created_at": now(), "mode": mode,
        "payload": json.dumps(decision_payload(d)),
    }]))  # fmt: skip


def _actuals(db: FootballDB, season: int, gw: int) -> pd.DataFrame:
    """Official points per player for the gameweek, summed over a double gameweek's fixtures.

    `explain` is the concatenation of every fixture's breakdown (a JSON list), so no fixture's
    scoring components are lost.
    """
    df = db.query(
        "SELECT player_id, fixture_id, total_points, minutes, explain FROM fpl_points_gw "
        "WHERE season = ? AND gw = ? AND player_id IS NOT NULL ORDER BY player_id, fixture_id",
        [season, gw],
    )
    if df.empty:
        return pd.DataFrame(columns=["player_id", "actual", "minutes", "explain", "fixtures"])
    rows = []
    for pid, g in df.groupby("player_id", sort=True):
        parts: list = []
        for blob in g["explain"]:
            if isinstance(blob, str) and blob:
                try:
                    parts += json.loads(blob)
                except ValueError:
                    continue
        rows.append({"player_id": int(pid), "actual": float(g["total_points"].sum()),
                     "minutes": float(g["minutes"].fillna(0).sum()),
                     "explain": json.dumps(parts), "fixtures": int(len(g))})  # fmt: skip
    return pd.DataFrame(rows)


def _explain_components(blob: str | None) -> dict[str, float]:
    out: dict[str, float] = {}
    for item in json.loads(blob or "[]"):
        out[item["identifier"]] = out.get(item["identifier"], 0.0) + float(item["points"])
    return out


def prediction_review(db: FootballDB, season: int, gw: int) -> dict[str, Any]:
    choice = pit.eligible_run(db, season, gw)
    final = pit.finality(db, season, gw)
    if not choice.found:
        log.info("GW%s not reviewed: %s", gw, choice.reason)
        return {"gw": gw, "available": False}
    proj = pit.run_projection(db, choice.run_id, season, gw)
    act = _actuals(db, season, gw)
    if proj.empty or act.empty:
        return {"gw": gw, "available": False}
    pos = db.query("SELECT player_id, position FROM players")
    df = proj.merge(act, on="player_id", how="left").merge(pos, on="player_id", how="left")
    missing = int(df["actual"].isna().sum())
    df = df[df["actual"].notna()].copy()  # no official row: unknown, never zero
    if df.empty:
        return {"gw": gw, "available": False}
    df["err"] = df["xpts"] - df["actual"]
    by_pos = {}
    for p, g in df.groupby("position"):
        by_pos[p] = {"n": len(g), "bias": round(float(g["err"].mean()), 3),
                     "mean_pred": round(float(g["xpts"].mean()), 4),
                     "mean_actual": round(float(g["actual"].mean()), 4),
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
    core = df[df["player_id"].isin(_field_core(db, season, gw))]
    report = {"gw": gw, "available": True, "n": len(df),
              "final": final.final, "final_reason": final.reason,
              "eligible": True, "missing_actuals": missing,
              "run": {"run_id": choice.run_id, "created_at": str(choice.created_at),
                      "deadline": str(choice.deadline), "deadline_source": choice.deadline_source,
                      "legacy": choice.legacy},
              "mae": round(float(df["err"].abs().mean()), 3),
              "rmse": round(float(np.sqrt((df["err"] ** 2).mean())), 3),
              "bias": round(float(df["err"].mean()), 3), "by_position": by_pos,
              "components": components, "flags": flags,
              "top20": {"predicted": round(float(top["xpts"].mean()), 2),
                        "actual": round(float(top["actual"].mean()), 2)},
              "template": {"n": len(core),
                           "mean_pred": round(float(core["xpts"].mean()), 4) if len(core) else 0,
                           "mean_actual": round(float(core["actual"].mean()), 4)
                           if len(core) else 0}}  # fmt: skip
    _store(db, season, gw, "prediction", report)
    return report


def _field_core(db: FootballDB, season: int, gw: int, floor: float = TEMPLATE) -> set[int]:
    """Players (canonical ids) owned by at least `floor` percent of the top-10k sample."""
    rows = db.con.execute(
        "SELECT DISTINCT p.player_id FROM field_ownership f JOIN fpl_players_gw p "
        "ON p.season = f.season AND p.fpl_id = f.fpl_id "
        "WHERE f.season = ? AND f.gw = ? AND f.owned >= ? AND p.player_id IS NOT NULL",
        [season, gw, floor],
    ).fetchall()
    return {int(r[0]) for r in rows}


def decision_review(db: FootballDB, season: int, gw: int) -> dict[str, Any]:
    """Score the last decision recorded before the deadline (the one that was issued); when the
    deadline is unknown or nothing came before it, the latest one, marked not eligible."""
    dl = pit.deadline(db, season, gw)
    row, eligible = None, False
    if dl.at is not None:
        row = db.con.execute(
            "SELECT payload FROM decisions WHERE season = ? AND gw = ? AND created_at < ? "
            "ORDER BY created_at DESC LIMIT 1",
            [season, gw, dl.at],
        ).fetchone()
        eligible = row is not None
    if row is None:
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
        "eligible": eligible, "final": pit.finality(db, season, gw).final,
        "missing_actuals": sum(1 for i in [*d["xi"], d["captain"]] if i not in pts),
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
