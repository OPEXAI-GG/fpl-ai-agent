"""League strength: how a player's numbers change when he moves between competitions.

Estimated from the movers themselves: for every player with enough minutes in competition A and
in the Premier League, the ratio of his Premier League rate to his rate in A, pooled over players
(weighted by the smaller of his two minute totals) and shrunk toward a prior with pseudo-minutes
so a thin sample cannot produce a wild factor. Attacking output is translated as a *share of the
team's xG* (so a player's old team being weaker or stronger is already accounted for); defensive
actions as per-90 counts.
"""

from __future__ import annotations

from datetime import datetime

import numpy as np
import pandas as pd

from fpl_agent.db.database import FootballDB, now

TARGET = "ENG1"
STATS = ("xg_share", "xa_share", "cbit", "cbirt")
# What we believe before any mover data arrives (Premier League = 1.0). Foreign top leagues are a
# little below; the Championship well below; European cups close to the Premier League.
PRIOR = {
    "ENG2": 0.62, "ENG3": 0.5, "ESP1": 0.9, "ITA1": 0.88, "GER1": 0.9, "FRA1": 0.82,
    "NED1": 0.7, "POR1": 0.68, "BEL1": 0.62, "SCO1": 0.55, "BRA1": 0.6,
    "UCL": 1.0, "UEL": 0.9, "UECL": 0.8,
}  # fmt: skip
PRIOR_DEFAULT = 0.6
PRIOR_MINUTES = 1500.0  # pseudo-minutes behind the prior


def prior(comp: str) -> float:
    return PRIOR.get(comp, PRIOR_DEFAULT)


def player_comp_rates(pm: pd.DataFrame) -> pd.DataFrame:
    """Per (player, competition): minutes and the four rates, pooled over all seasons."""
    d = pm[pm["minutes"] > 0].copy()
    d["exposure"] = d["team_xg"] * d["minutes"] / 90.0
    g = d.groupby(["player_id", "comp"])
    out = g.agg(
        minutes=("minutes", "sum"), xg=("xg", "sum"), xa=("xa", "sum"),
        cbit=("cbit", "sum"), cbirt=("cbirt", "sum"), exposure=("exposure", "sum"),
    ).reset_index()  # fmt: skip
    out["xg_share"] = out["xg"] / out["exposure"].replace(0, np.nan)
    out["xa_share"] = out["xa"] / out["exposure"].replace(0, np.nan)
    out["cbit"] = out["cbit"] / out["minutes"] * 90
    out["cbirt"] = out["cbirt"] / out["minutes"] * 90
    return out


def estimate(
    pm: pd.DataFrame,
    target: str = TARGET,
    min_minutes: float = 450.0,
    prior_minutes: float = PRIOR_MINUTES,
) -> pd.DataFrame:
    """Translation factors (from_comp -> target) per stat, from players seen in both."""
    rates = player_comp_rates(pm)
    rates = rates[rates["minutes"] >= min_minutes]
    to = rates[rates["comp"] == target].set_index("player_id")
    rows = []
    for comp in sorted(set(rates["comp"]) - {target}):
        frm = rates[rates["comp"] == comp].set_index("player_id")
        both = frm.index.intersection(to.index)
        for stat in STATS:
            fac, n, mins = prior(comp), 0, 0.0
            if len(both):
                a, b = frm.loc[both, stat], to.loc[both, stat]
                ok = a.notna() & b.notna() & (a > 0)
                if ok.any():
                    w = np.minimum(frm.loc[both, "minutes"], to.loc[both, "minutes"])[ok]
                    mean_from = float(rates.loc[(rates["comp"] == comp), stat].mean())
                    num = float((w * b[ok]).sum() + prior_minutes * prior(comp) * mean_from)
                    den = float((w * a[ok]).sum() + prior_minutes * mean_from)
                    fac, n, mins = num / den, int(ok.sum()), float(w.sum())
            rows.append({"from_comp": comp, "to_comp": target, "stat": stat,
                         "factor": float(np.clip(fac, 0.2, 1.5)), "n_players": n,
                         "minutes": mins})  # fmt: skip
    return pd.DataFrame(rows, columns=["from_comp", "to_comp", "stat", "factor", "n_players",
                                       "minutes"])  # fmt: skip


def store(db: FootballDB, table: pd.DataFrame) -> int:
    t = table.assign(updated_at=now().replace(microsecond=0))  # UTC, like every other stamp
    return db.upsert("league_translation", t)


class Translations:
    """Lookup of factors; the target competition is 1.0 and unknown ones use the prior."""

    def __init__(self, table: pd.DataFrame | None = None, target: str = TARGET) -> None:
        self.target = target
        self._f = {}
        if table is not None and not table.empty:
            self._f = {(r.from_comp, r.stat): r.factor for r in table.itertuples()}

    @classmethod
    def load(cls, db: FootballDB, as_of: datetime | None = None) -> Translations:
        """Stored factors; with `as_of`, only ones fitted by then (later fits are unknown to a
        decision made at that time, which falls back to the priors)."""
        if as_of is None:
            return cls(db.query("SELECT * FROM league_translation"))
        return cls(db.query("SELECT * FROM league_translation WHERE updated_at <= ?", [as_of]))

    def factor(self, comp: str, stat: str) -> float:
        if comp == self.target:
            return 1.0
        return self._f.get((comp, stat), prior(comp))


def validate(pm: pd.DataFrame, target: str = TARGET, min_minutes: float = 450.0) -> dict:
    """Leave-one-mover-out check: predict each mover's target-league rate from his old-league
    rate with and without translation, and report the mean absolute errors."""
    rates = player_comp_rates(pm)
    rates = rates[rates["minutes"] >= min_minutes]
    to = rates[rates["comp"] == target].set_index("player_id")
    out = {}
    for stat in STATS:
        errs_t, errs_n = [], []
        for comp in set(rates["comp"]) - {target}:
            frm = rates[rates["comp"] == comp].set_index("player_id")
            both = list(frm.index.intersection(to.index))
            for pid in both:
                others = pm[pm["player_id"] != pid]
                table = estimate(others, target, min_minutes)
                f = Translations(table, target).factor(comp, stat)
                a, b = frm.loc[pid, stat], to.loc[pid, stat]
                if pd.notna(a) and pd.notna(b):
                    errs_t.append(abs(a * f - b))
                    errs_n.append(abs(a - b))
        if errs_t:
            out[stat] = {
                "mae_translated": float(np.mean(errs_t)),
                "mae_naive": float(np.mean(errs_n)),
                "n": len(errs_t),
            }
    return out
