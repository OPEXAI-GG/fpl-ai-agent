"""Bookmaker odds -> expected goals, and blending them with the model.

Odds carry information the stats do not (team news, rotation, form people watched). The 1X2 price
(plus the over/under 2.5 price when there is one) is turned into the pair of Poisson goal rates
that reproduces it, after removing the bookmaker's margin proportionally.
"""

from __future__ import annotations

import numpy as np
from scipy.optimize import least_squares
from scipy.stats import poisson

from fpl_agent.model.strength import goal_matrix


def remove_margin(*odds: float) -> list[float]:
    """Decimal odds -> probabilities summing to 1 (proportional margin removal)."""
    if any(o is None or not np.isfinite(o) or o <= 1.0 for o in odds):
        raise ValueError(f"invalid decimal odds {odds}")
    raw = [1.0 / o for o in odds]
    total = sum(raw)
    return [r / total for r in raw]


def implied_lambdas(
    home: float, draw: float, away: float, over25: float | None = None, under25: float | None = None
) -> tuple[float, float]:
    """Poisson rates (home, away) matching the market's win/draw/lose and, if given, totals."""
    p_h, p_d, p_a = remove_margin(home, draw, away)
    p_over = None
    if over25 and under25:
        p_over = remove_margin(over25, under25)[0]

    def residual(x):
        lh, la = np.exp(x)
        mat = goal_matrix(lh, la)
        res = [np.tril(mat, -1).sum() - p_h, np.trace(mat) - p_d, np.triu(mat, 1).sum() - p_a]
        if p_over is not None:
            totals = np.add.outer(np.arange(mat.shape[0]), np.arange(mat.shape[1]))
            res.append(mat[totals >= 3].sum() - p_over)
        return res

    sol = least_squares(residual, x0=np.log([1.4, 1.1]), bounds=(np.log(0.05), np.log(8.0)))
    lh, la = np.exp(sol.x)
    return float(lh), float(la)


def blend(model: tuple[float, float], market: tuple[float, float] | None, w_market: float = 0.5):
    """Geometric blend of model and market goal rates; the model alone when there is no price."""
    if market is None:
        return model
    w = min(max(w_market, 0.0), 1.0)
    return (model[0] ** (1 - w) * market[0] ** w, model[1] ** (1 - w) * market[1] ** w)


def p_over(lh: float, la: float, line: int = 2) -> float:
    """P(total goals > line) for integer lines (2 -> over 2.5)."""
    return float(1.0 - poisson.cdf(line, lh + la))
