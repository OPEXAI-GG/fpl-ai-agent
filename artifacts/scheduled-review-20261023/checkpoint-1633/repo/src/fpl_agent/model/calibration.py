"""Learning from the agent's own results.

Two things make each week's real outcomes change the next week's plan:

1. **Refit.** Every run refits the models on all history, which by then includes the gameweeks
   just played (minutes, shares, finishing, team strength): that is the main learning path.
2. **Calibration.** The review loop stores, per position, what the projections averaged and what
   players actually scored. This module turns those reviews into a gentle multiplicative
   correction per position. It is shrunk toward 1 (little data = little change), capped at
   +-10%, and only built from reviews of gameweeks that were already over, so it can never see
   the future.

The correction and its inputs are reported in the weekly output so it is never a black box.
"""

from __future__ import annotations

import json

import numpy as np

from fpl_agent.db.database import FootballDB
from fpl_agent.model.project import Projection

CAP = 0.10  # at most +-10%
PRIOR_N = 4000  # player-gameweeks of evidence needed to trust the data as much as the model


def factors(db: FootballDB, season: int, before_gw: int) -> dict[str, float]:
    """Per-position multipliers from reviews of gameweeks before `before_gw`."""
    rows = db.query(
        "SELECT payload FROM reviews WHERE season = ? AND kind = 'prediction' AND gw < ?",
        [season, before_gw],
    )
    pred: dict[str, float] = {}
    act: dict[str, float] = {}
    n: dict[str, int] = {}
    for blob in rows["payload"]:
        rep = json.loads(blob)
        for pos, m in (rep.get("by_position") or {}).items():
            if "mean_pred" not in m:
                continue
            pred[pos] = pred.get(pos, 0.0) + m["mean_pred"] * m["n"]
            act[pos] = act.get(pos, 0.0) + m["mean_actual"] * m["n"]
            n[pos] = n.get(pos, 0) + m["n"]
    out = {}
    for pos, count in n.items():
        if pred[pos] <= 0:
            continue
        raw = act[pos] / pred[pos]
        w = count / (count + PRIOR_N)
        out[pos] = float(np.clip(1.0 + w * (raw - 1.0), 1 - CAP, 1 + CAP))
    return out


def apply(proj: Projection, fac: dict[str, float]) -> Projection:
    """Scale each player's sampled points by his position's factor (in place)."""
    for i, pos in enumerate(proj.meta["position"]):
        f = fac.get(pos, 1.0)
        if f != 1.0:
            proj.points[i] *= np.float32(f)
    return proj
