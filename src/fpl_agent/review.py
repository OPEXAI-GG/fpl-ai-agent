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
from fpl_agent.db.fpl_identity import reader_sources
from fpl_agent.lessons import TEMPLATE
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
        "field_view": d.field_view, "checks": d.checks,
        "chip_timing": d.chip_report.get("timing", {}),
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
    """Official points per player for the gameweek, every fixture summed; `explain` holds the
    breakdown of all of a double gameweek's fixtures, not just one."""
    rows = db.query(
        "SELECT player_id, total_points, minutes, explain FROM fpl_points_gw "
        "WHERE season = ? AND gw = ? AND player_id IS NOT NULL ORDER BY player_id, fixture_id",
        [season, gw],
    )
    if rows.empty:
        return pd.DataFrame(columns=["player_id", "actual", "minutes", "explain"])
    out = []
    for pid, g in rows.groupby("player_id", sort=True):
        items = [x for blob in g["explain"] for x in json.loads(blob or "[]")]
        out.append({"player_id": pid, "actual": float(g["total_points"].sum()),
                    "minutes": float(g["minutes"].fillna(0).sum()),
                    "explain": json.dumps(items)})  # fmt: skip
    return pd.DataFrame(out)


def _positions(db: FootballDB, season: int, gw: int) -> pd.DataFrame:
    """Each player's FPL position in that season (players change position between seasons)."""
    return db.query(
        "SELECT player_id, position FROM fpl_players_gw WHERE season = ? AND gw <= ? "
        "AND player_id IS NOT NULL QUALIFY row_number() OVER (PARTITION BY player_id "
        "ORDER BY gw DESC, snapshot_at DESC) = 1",
        [season, gw],
    )


def _cutoff(db: FootballDB, season: int, gw: int):
    """The gameweek's decision cutoff: the official deadline when one was observed (stored from
    2026-10-23 on), otherwise 90 minutes before its first kickoff (FPL's usual deadline). None
    without either."""
    from fpl_agent.db.gameweeks import official_deadline
    from fpl_agent.model.project import approx_deadline

    official = official_deadline(db, season, gw)
    if official is not None:
        return official

    first = db.con.execute(
        "SELECT min(m.kickoff) FROM matches m JOIN match_ids i USING (match_id) "
        "WHERE i.source IN (?, ?) AND m.season = ? AND m.matchday = ?",
        [*reader_sources(season), season, gw],
    ).fetchone()[0]
    return approx_deadline(first) if first is not None else None


PRE_DEADLINE = "newest run before the deadline (first kickoff - 90 min)"


def projection_run(db: FootballDB, season: int, gw: int) -> tuple[str | None, str]:
    """(run id, how it was chosen): the newest single projection run saved before the
    gameweek's cutoff, so every player is judged on the same forecast the decision saw."""
    cut = _cutoff(db, season, gw)
    if cut is not None:
        row = db.con.execute(
            "SELECT run_id FROM projections WHERE season = ? AND gw = ? AND created_at <= ? "
            "ORDER BY created_at DESC LIMIT 1",
            [season, gw, cut],
        ).fetchone()
        if row:
            return row[0], PRE_DEADLINE
    row = db.con.execute(
        "SELECT run_id FROM projections WHERE season = ? AND gw = ? ORDER BY created_at LIMIT 1",
        [season, gw],
    ).fetchone()
    return (row[0], "no run before the deadline: the earliest run (may have seen team news)"
            ) if row else (None, "no projection")  # fmt: skip


def _explain_components(blob: str | None) -> dict[str, float]:
    out: dict[str, float] = {}
    for item in json.loads(blob or "[]"):
        out[item["identifier"]] = out.get(item["identifier"], 0.0) + float(item["points"])
    return out


def prediction_review(db: FootballDB, season: int, gw: int) -> dict[str, Any]:
    """Projection error for a finished gameweek. Only players with an official result are
    scored: a projected player with no result row is counted in `n_missing_outcome`, never
    scored as zero. `eligible_for_calibration` is true only for a forecast made before the
    deadline; `calibration.factors` ignores every other review."""
    run, chosen = projection_run(db, season, gw)
    pre_deadline = chosen == PRE_DEADLINE
    proj = db.query(
        "SELECT player_id, xpts, detail FROM projections WHERE run_id = ? AND gw = ?", [run, gw]
    )
    act = _actuals(db, season, gw)
    if proj.empty or act.empty:
        return {"gw": gw, "available": False}
    pos = _positions(db, season, gw)
    df = proj.merge(act, on="player_id", how="left").merge(pos, on="player_id", how="left")
    missing = int(df["actual"].isna().sum())
    df = df[df["actual"].notna()].copy()
    if df.empty:
        return {"gw": gw, "available": False, "n_missing_outcome": missing}
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
    report = {"gw": gw, "available": True, "n": len(df), "projection_run": run,
              "projection_choice": chosen, "pre_deadline": pre_deadline,
              "eligible_for_calibration": pre_deadline, "n_missing_outcome": missing,
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
    """How the agent's plan for the gameweek would have scored under FPL's scoring: automatic
    substitutions, the vice-captain covering a captain who did not play, and the plan's chip
    (Bench Boost, Triple Captain). This is the *hypothetical plan*: the points your real team
    scored (with your own changes and official hits) are reviewed by `lessons`. A plan that was
    sent and verified (`live-done`) is preferred over later re-plans."""
    from fpl_agent.model.replay import score_week
    from fpl_agent.rules import Rules
    from fpl_agent.solver.planner import WeekPlan

    row = db.con.execute(
        "SELECT payload, mode FROM decisions WHERE season = ? AND gw = ? "
        "ORDER BY mode = 'live-done' DESC, created_at DESC LIMIT 1",
        [season, gw],
    ).fetchone()
    act = _actuals(db, season, gw).set_index("player_id")
    if not row or act.empty:
        return {"gw": gw, "available": False}
    d = json.loads(row[0])
    pts = {int(k): int(v) for k, v in act["actual"].items()}
    mins = {int(k): int(v) for k, v in act["minutes"].items()}
    pos = dict(_positions(db, season, gw).itertuples(index=False, name=None))
    squad = list(d["xi"]) + list(d["bench"])
    if any(p not in pos for p in squad):
        return {"gw": gw, "available": False, "reason": "a planned player has no position"}
    chip = d.get("chip") if d.get("chip") in ("bboost", "3xc") else None
    wk = WeekPlan(gw, squad, list(d["xi"]), list(d["bench"]), d["captain"], d["vice"], [], [],
                  0, chip, 0, 0.0)  # fmt: skip
    total, cap_pts, bench_left = score_week(wk, pts, mins, pos, Rules.nearest(season))
    got = lambda ids: [float(pts.get(i, 0)) for i in ids]  # noqa: E731
    best_cap = max(got(d["xi"]), default=0.0)
    gain_in = sum(got(d["transfers_in"])) - sum(got(d["transfers_out"]))
    report = {
        "gw": gw, "available": True, "action": d.get("action"), "mode": row[1],
        "scoring": "hypothetical plan with auto-subs, vice cover and chip (current rules)",
        "xi_points": total, "bench_points_left": bench_left, "chip": chip,
        "hits": d["hits"], "points_after_hits": total - 4 * d["hits"],
        "captain_points": cap_pts, "best_possible_captain": best_cap,
        "captain_regret": best_cap - cap_pts,
        "transfer_gain": gain_in, "transfer_gain_after_hits": gain_in - 4 * d["hits"],
        "predicted": d["xp"], "predicted_vs_actual": round(d["xp"] - total, 2),
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
