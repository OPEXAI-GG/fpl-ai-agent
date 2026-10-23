"""Evaluate doubtful-player starts with actual simulated automatic substitutions."""

from dataclasses import replace
from itertools import permutations

import numpy as np

from fpl_agent.rules.engine import _valid_formation
from fpl_agent.solver.chips import score_plan
from fpl_agent.solver.inputs import _startable
from fpl_agent.solver.planner import Plan


def refine_doubtful_lineup(week, proj, rules):
    """Bounded first-week swap search; no transfers and no forced start for a named player.

    Only triggered by an owned doubt. Compare all samples on the same worlds. It includes
    zero-minute cover, cameo failures and captain fallback, unlike a constant bench weight.
    This is a local lineup refinement, not a globally optimal squad/transfer search.
    """
    meta = {r.player_id: r for r in proj.meta.itertuples()}
    doubts = [
        p
        for p in week.squad
        if p in meta and meta[p].status == "d" and _startable(meta[p].status, meta[p].chance)
    ]
    if not doubts or not set(week.squad) <= set(meta) or week.chip == "bboost":
        return None
    pos = {p: r.position for p, r in meta.items()}
    xp = dict(zip(proj.player_ids, proj.mean()[:, 0], strict=True))
    worlds = np.arange(proj.points.shape[2])

    def evaluate(w):
        return float(score_plan(Plan([w], 0.0, 0, 0), proj, rules, worlds).mean())

    original = evaluate(week)
    best, best_score = week, original
    for doubt in doubts:
        # Starting and benching the doubt are both evaluated; one-minute cameos never sub out.
        current = best
        swaps = current.bench if doubt in current.xi else current.xi
        for other in [None, *swaps]:
            xi = list(current.xi)
            if other is not None:
                incoming, outgoing = (other, doubt) if doubt in xi else (doubt, other)
                if not _startable(meta[incoming].status, meta[incoming].chance):
                    continue
                xi[xi.index(outgoing)] = incoming
            if not _valid_formation([pos[p] for p in xi], rules):
                continue
            bench = [p for p in current.squad if p not in xi]
            outfield = [p for p in bench if pos[p] != "GKP"]
            keepers = [p for p in bench if pos[p] == "GKP"]
            cap = current.captain if current.captain in xi else max(xi, key=xp.get)
            vice = (
                current.vice
                if current.vice in xi and current.vice != cap
                else max((p for p in xi if p != cap), key=xp.get)
            )
            for order in permutations(outfield):
                trial = replace(current, xi=xi, bench=[*order, *keepers], captain=cap, vice=vice)
                score = evaluate(trial)
                if score > best_score + 1e-6:
                    best, best_score = trial, score
    week.xi, week.bench, week.captain, week.vice = best.xi, best.bench, best.captain, best.vice
    week.xp = float(
        sum(xp[p] for p in week.xi) + xp[week.captain] * (2 if week.chip == "3xc" else 1)
    )
    return {"before": original, "after": best_score, "samples": len(worlds)}
