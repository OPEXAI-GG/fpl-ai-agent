"""L2 team strength: Dixon-Coles style attack/defence ratings per team.

    log lambda_home = mu[comp] + home + att[home] + dfn[away]
    log lambda_away = mu[comp]        + att[away] + dfn[home]

`dfn` is how many goals a team concedes relative to average (higher = worse defence). Teams carry
one rating across competitions and seasons, so a promoted club's Championship results are on the
same scale as its Premier League ones through the clubs that moved between the two. Targets are a
blend of goals and xG (xG is the less noisy signal), matches are weighted by exp-decay in age, and
ratings are ridge-shrunk toward a prior set by the tier a club last played in, so a promoted club
starts below an established top-flight club and moves quickly as evidence arrives.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

import numpy as np
import pandas as pd
from scipy.optimize import minimize

from fpl_agent.db.database import FootballDB

MATCH_COLUMNS = ["match_date", "comp", "tier", "home_id", "away_id", "hg", "ag", "hxg", "axg"]


@dataclass
class TeamRatings:
    mu: dict[str, float]
    home: float
    att: dict[int, float]
    dfn: dict[int, float]
    tier_of: dict[int, int] = field(default_factory=dict)  # latest tier each team was seen in
    tier_prior: float = 0.18
    default_comp: str = "ENG1"

    def lambdas(self, home_id: int, away_id: int, comp: str | None = None) -> tuple[float, float]:
        mu = self.mu.get(comp or self.default_comp, float(np.mean(list(self.mu.values()))))
        att, dfn = self.att, self.dfn
        lh = np.exp(mu + self.home + att.get(home_id, 0.0) + dfn.get(away_id, 0.0))
        la = np.exp(mu + att.get(away_id, 0.0) + dfn.get(home_id, 0.0))
        return float(lh), float(la)

    def strength_table(self) -> pd.DataFrame:
        ids = sorted(set(self.att) | set(self.dfn))
        return pd.DataFrame(
            {"team_id": ids, "att": [self.att.get(i, 0.0) for i in ids],
             "dfn": [self.dfn.get(i, 0.0) for i in ids]}
        )  # fmt: skip


def load_matches(db: FootballDB, as_of: date | None = None, comps: tuple[str, ...] | None = None):
    """Finished matches with goals and (where any source has it) xG, one row per match.

    xG is averaged over the sources that have it; goals come from the canonical match row.
    """
    sql = """
        SELECT m.match_date, m.competition_id AS comp, coalesce(c.tier, 1) AS tier,
               m.home_team_id AS home_id, m.away_team_id AS away_id,
               m.home_goals AS hg, m.away_goals AS ag,
               avg(CASE WHEN t.is_home THEN t.xg_for END) AS hxg,
               avg(CASE WHEN NOT t.is_home THEN t.xg_for END) AS axg
        FROM matches m
        LEFT JOIN competitions c ON c.competition_id = m.competition_id
        LEFT JOIN team_match t ON t.match_id = m.match_id
        WHERE m.finished AND m.home_goals IS NOT NULL
    """
    params: list = []
    if as_of is not None:
        sql += " AND m.match_date < ?"
        params.append(as_of)
    if comps:
        sql += f" AND m.competition_id IN ({', '.join('?' for _ in comps)})"
        params += list(comps)
    sql += " GROUP BY ALL"
    df = db.query(sql, params)
    df["match_date"] = pd.to_datetime(df["match_date"]).dt.date
    return df[MATCH_COLUMNS]


def fit_ratings(
    matches: pd.DataFrame,
    as_of: date,
    half_life_days: float = 330.0,
    xg_weight: float = 0.6,
    ridge: float = 3.0,
    tier_prior: float = 0.18,
    default_comp: str = "ENG1",
) -> TeamRatings:
    """Fit ratings on matches before `as_of`. With no matches the result is a neutral model."""
    m = matches[matches["match_date"] < as_of]
    if m.empty:
        return TeamRatings({default_comp: 0.1}, 0.2, {}, {}, tier_prior=tier_prior,
                           default_comp=default_comp)  # fmt: skip
    teams = sorted(set(m["home_id"]) | set(m["away_id"]))
    tix = {t: i for i, t in enumerate(teams)}
    comps = sorted(set(m["comp"]))
    cix = {c: i for i, c in enumerate(comps)}
    n_t, n_c = len(teams), len(comps)
    h = m["home_id"].map(tix).to_numpy()
    a = m["away_id"].map(tix).to_numpy()
    c = m["comp"].map(cix).to_numpy()
    age = np.array([(as_of - d).days for d in m["match_date"]], dtype=float)
    w = 0.5 ** (age / half_life_days)

    def target(goals, xg):
        g = m[goals].to_numpy(dtype=float)
        x = m[xg].to_numpy(dtype=float)
        return np.where(np.isnan(x), g, xg_weight * x + (1 - xg_weight) * g)

    yh, ya = target("hg", "hxg"), target("ag", "axg")

    # prior mean per team: lower tiers start weaker (attack lower, defence leakier)
    last_tier = (
        pd.concat([m[["match_date", "home_id", "tier"]].set_axis(["d", "t", "tier"], axis=1),
                   m[["match_date", "away_id", "tier"]].set_axis(["d", "t", "tier"], axis=1)])
        .sort_values("d").groupby("t")["tier"].last()
    )  # fmt: skip
    top = int(last_tier.min())
    depth = np.array([max(int(last_tier[t]) - top, 0) for t in teams], dtype=float)
    prior_att = -tier_prior * depth
    prior_dfn = tier_prior * depth

    def unpack(x):
        return x[:n_c], x[n_c], x[n_c + 1 : n_c + 1 + n_t], x[n_c + 1 + n_t :]

    def fun(x):
        mu, hadv, att, dfn = unpack(x)
        eh = mu[c] + hadv + att[h] + dfn[a]
        ea = mu[c] + att[a] + dfn[h]
        lh, la = np.exp(eh), np.exp(ea)
        nll = np.sum(w * (lh - yh * eh)) + np.sum(w * (la - ya * ea))
        pa, pd_ = att - prior_att, dfn - prior_dfn
        nll += ridge * (pa @ pa + pd_ @ pd_)
        dh, da = w * (lh - yh), w * (la - ya)
        g_mu = np.bincount(c, dh + da, minlength=n_c)
        g_h = np.sum(dh)
        g_att = np.bincount(h, dh, minlength=n_t) + np.bincount(a, da, minlength=n_t)
        g_dfn = np.bincount(a, dh, minlength=n_t) + np.bincount(h, da, minlength=n_t)
        g_att += 2 * ridge * pa
        g_dfn += 2 * ridge * pd_
        return nll, np.concatenate([g_mu, [g_h], g_att, g_dfn])

    x0 = np.concatenate([np.full(n_c, 0.1), [0.2], prior_att, prior_dfn])
    res = minimize(fun, x0, jac=True, method="L-BFGS-B", options={"maxiter": 500})
    mu, hadv, att, dfn = unpack(res.x)
    return TeamRatings(
        mu={comps[i]: float(mu[i]) for i in range(n_c)},
        home=float(hadv),
        att={teams[i]: float(att[i]) for i in range(n_t)},
        dfn={teams[i]: float(dfn[i]) for i in range(n_t)},
        tier_of={t: int(last_tier[t]) for t in teams},
        tier_prior=tier_prior,
        default_comp=default_comp,
    )


def goal_matrix(lh: float, la: float, max_goals: int = 20) -> np.ndarray:
    """P(home goals = i, away goals = j) under independent Poisson goals."""
    from scipy.stats import poisson

    ph = poisson.pmf(np.arange(max_goals + 1), lh)
    pa = poisson.pmf(np.arange(max_goals + 1), la)
    return np.outer(ph, pa)


def outcome_probs(lh: float, la: float) -> dict[str, float]:
    mat = goal_matrix(lh, la)
    return {
        "home": float(np.tril(mat, -1).sum()),
        "draw": float(np.trace(mat)),
        "away": float(np.triu(mat, 1).sum()),
        "home_cs": float(mat[:, 0].sum()),  # away team scores 0
        "away_cs": float(mat[0, :].sum()),
    }
