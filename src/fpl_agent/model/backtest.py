"""Point-in-time accuracy backtest for the projection stack.

For each gameweek the models see only what existed before its deadline (approximated as 90
minutes before the first kickoff, since historical deadlines are not stored): history up to the
previous gameweek, matches before that date, the player snapshot, odds and league translations
as of then, never closing odds. Bookmaker prices whose observation time is unknown are an
event-time reconstruction, not known-at data. The gameweek is projected and scored against the
official points. The same rows are scored for two baselines: the player's average over his last
five fixtures, and FPL's own expected points (`xP`) where the archive has them. Results are
reported separately for GK, DEF, MID and FWD, as the plan requires.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from fpl_agent.db.database import FootballDB
from fpl_agent.model import features, minutes, project, strength
from fpl_agent.model.simulate import SimConfig
from fpl_agent.rules import Rules


def actual_points(db: FootballDB, season: int) -> pd.DataFrame:
    return db.query(
        "SELECT player_id, gw, sum(total_points) AS actual, sum(minutes) AS minutes, "
        "count(*) AS fixtures FROM fpl_points_gw WHERE season = ? AND player_id IS NOT NULL "
        "GROUP BY player_id, gw", [season])  # fmt: skip


def projection_backtest(
    db: FootballDB,
    season: int,
    gws: list[int],
    cfg: SimConfig | None = None,
    refit_every: int = 6,
    rules: Rules | None = None,
    validated: bool = False,
) -> pd.DataFrame:
    """Every fit and baseline uses only the history visible at each gameweek's cutoff
    (`project.history_as_of`); a model reused across gameweeks was fitted at an earlier cutoff.
    `validated`: only eligible gameweeks (else NotEligible), cut at their official deadline,
    with excluded matches removed from every model input and from scoring
    (`db.eligibility.validated_inputs`), and only odds observed by the cutoff (no event-time
    reconstruction). Without it the result is exploratory."""
    cfg = cfg or SimConfig(n=800)
    frame = features.player_gw_frame(db)
    pm_all = features.match_frame(db)
    matches = strength.load_matches(db)
    cuts, touched = {}, set()
    if validated:
        from fpl_agent.db.eligibility import validated_inputs

        full = frame
        frame, pm_all, cuts, skip = validated_inputs(db, season, gws, frame, pm_all)
        # player-gameweeks that include an excluded match are not scored either
        hit = full[[(int(a), int(b)) in skip for a, b in zip(full["season"], full["fixture_id"],
                                                             strict=True)]]  # fmt: skip
        touched = set(zip(hit.loc[hit["season"] == season, "player_id"],
                          hit.loc[hit["season"] == season, "gw"], strict=True))  # fmt: skip
    actual = actual_points(db, season).set_index(["player_id", "gw"])
    rules = rules or Rules.nearest(season)
    rows, model, fitted_at = [], None, None
    for gw in gws:
        fx = project.fixtures_for(db, season, [gw])
        if fx.empty:
            continue
        cutoff = cuts.get(gw) or project.approx_deadline(fx["kickoff"].min())
        as_of = cutoff.date()
        # the cutoff applies to every fit and baseline, not only inside the projection: a
        # model fitted on the whole-database frame would carry later snapshots into it
        history = project.history_as_of(frame, db, season, gw, cutoff)
        if len(history) < 200:
            continue
        if model is None or fitted_at is None or gw - fitted_at >= refit_every:
            model, fitted_at = minutes.MinutesModel.fit(history), gw
        ratings = strength.fit_ratings(matches, as_of)
        proj = project.build_projection(
            db, season, [gw], as_of=as_of, cfg=cfg, rules=rules, minutes_model=model,
            ratings=ratings, frame=frame, pm=pm_all, pool_gw=gw, cutoff=cutoff,
            known_at_odds=validated,
        )  # fmt: skip
        f = proj.frame().set_index("player_id")
        hist5 = history.sort_values(["player_id", "season", "gw"]).groupby("player_id").tail(5)
        last5 = hist5.groupby("player_id")["points"].mean()
        xp = (
            frame[(frame["season"] == season) & (frame["gw"] == gw)]
            .groupby("player_id")["xp"]
            .sum(min_count=1)
        )
        for pid, r in f.iterrows():
            if (pid, gw) not in actual.index or (pid, gw) in touched:
                continue
            a = actual.loc[(pid, gw)]
            rows.append({
                "player_id": pid, "gw": gw, "position": r["position"],
                "actual": a["actual"], "sim": r["xpts"],
                "last5": last5.get(pid, np.nan) * a["fixtures"],
                "fpl_xp": xp.get(pid, np.nan),
            })  # fmt: skip
    return pd.DataFrame(rows)


def summarize(results: pd.DataFrame) -> pd.DataFrame:
    """MAE / RMSE / correlation / bias of each predictor, per position and overall."""
    out = []
    for pos, g in [*results.groupby("position"), ("ALL", results)]:
        for name in ("sim", "last5", "fpl_xp"):
            d = g.dropna(subset=[name])
            if len(d) < 20:
                continue
            err = d[name] - d["actual"]
            out.append({"position": pos, "predictor": name, "n": len(d),
                        "mae": err.abs().mean(), "rmse": float(np.sqrt((err**2).mean())),
                        "corr": float(np.corrcoef(d[name], d["actual"])[0, 1]),
                        "bias": err.mean()})  # fmt: skip
    return pd.DataFrame(out)


def beats_baselines(summary: pd.DataFrame, metric: str = "rmse") -> dict[str, bool]:
    """Per position: does the simulator beat each baseline on `metric` (lower is better)?"""
    verdict = {}
    for pos, g in summary.groupby("position"):
        sim = g[g.predictor == "sim"]
        if sim.empty:
            continue
        for base in ("last5", "fpl_xp"):
            b = g[g.predictor == base]
            if not b.empty:
                verdict[f"{pos}:{base}"] = bool(sim[metric].iloc[0] < b[metric].iloc[0])
    return verdict
