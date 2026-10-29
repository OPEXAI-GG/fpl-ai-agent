"""L4 player event rates: what each player does per minute on the pitch.

* **Attacking share**: the fraction of his team's xG (and xA) a player produces per minute on the
  pitch, measured against the team's own xG in the same matches. Recent seasons weigh more, other
  competitions are translated by league strength, and every estimate is shrunk toward the position
  average with pseudo-exposure, so a hot streak or a small sample is not over-trusted. Finishing
  (goals above xG) is kept but heavily shrunk: it is mostly luck.
* **Defensive contribution** (the DefCon quantity): per-90 counts, negative-binomial, position-
  specific (CBIT for defenders, CBIRT for everyone else).
* **Discipline**: cards per 90, shrunk to the position average.

Players with no usable history simply get the position prior, flagged `source = "prior"`.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from fpl_agent.model.translation import TARGET, Translations

POS_SHARE_DEFAULT = {"GKP": 0.0, "DEF": 0.04, "MID": 0.09, "FWD": 0.22}
POS_CBIT_DEFAULT = {"GKP": 1.0, "DEF": 7.0, "MID": 8.0, "FWD": 5.0}
SEASON_DECAY = 0.6  # weight multiplier per season of age
EXPOSURE_PRIOR = 12.0  # pseudo team-xG behind the position prior for shares
FINISH_PRIOR_XG = 25.0
CBIT_PRIOR_MINUTES = 600.0
CARD_PRIOR_MINUTES = 900.0
FRAME_COLUMNS = [
    "player_id", "position", "xg_share", "xa_share", "finish", "cbit_mu", "cbit_r", "yellow90",
    "red90", "evidence_minutes", "source", "xg_source", "xa_source",
]  # fmt: skip


def _recency(season: pd.Series, current: int) -> np.ndarray:
    return SEASON_DECAY ** (current - season.to_numpy(dtype=float)).clip(min=0)


def position_priors(pm: pd.DataFrame, target: str = TARGET) -> dict[str, dict[str, float]]:
    """Position averages measured on target-competition rows; defaults when there are none.
    `share` is a position's share of its team's xG, `xa_share` its share of the team's xA
    (measured separately: assists are spread differently from goals); without xA data the
    assist prior falls back to 0.8 x the goal share, as before."""
    share = dict(POS_SHARE_DEFAULT)
    cbit = dict(POS_CBIT_DEFAULT)
    xa_share = {p: 0.8 * s for p, s in share.items()}
    d = pm[(pm["comp"] == target) & (pm["minutes"] > 0)]
    if len(d) > 200:
        d = d.assign(expo=d["team_xg"] * d["minutes"] / 90.0)
        for pos, g in d.groupby("position"):
            goal = g[g["xg"].notna() & g["expo"].notna()]
            assist = g[g["xa"].notna() & g["expo"].notna()]
            if goal["expo"].sum() > 20:
                share[pos] = float(goal["xg"].sum() / goal["expo"].sum())
                xa_share[pos] = 0.8 * share[pos]
            if assist["expo"].sum() > 20:
                xa_share[pos] = float(assist["xa"].sum() / assist["expo"].sum())
        for pos, g in d.groupby("position"):
            col = "cbit" if pos == "DEF" else "cbirt"
            if g[col].notna().sum() > 100:
                known = g[g[col].notna()]
                cbit[pos] = float(known[col].sum() / known["minutes"].sum() * 90)
    return {"share": share, "cbit": cbit, "xa_share": xa_share}


def nb_dispersion(
    pm: pd.DataFrame, positions: dict[int, str], target: str = TARGET
) -> dict[str, float]:
    """Negative-binomial size `r` per position by method of moments on per-90 counts."""
    out = {}
    d = pm[(pm["comp"] == target) & (pm["minutes"] >= 60)].copy()
    d["pos"] = d["player_id"].map(positions)
    for pos, g in d.groupby("pos"):
        col = "cbit" if pos == "DEF" else "cbirt"
        x = g[col].dropna() / g.loc[g[col].notna(), "minutes"] * 90
        if len(x) < 100:
            continue
        mu, var = float(x.mean()), float(x.var())
        out[pos] = float(np.clip(mu**2 / (var - mu), 2.0, 200.0)) if var > mu else 200.0
    return out


def fit_player_rates(
    pm: pd.DataFrame,
    frame: pd.DataFrame,
    positions: dict[int, str],
    current_season: int,
    translations: Translations | None = None,
    target: str = TARGET,
    as_of=None,
    fpl_xg_fallback: bool = False,
) -> pd.DataFrame:
    """Rates for every player in `positions` (player_id -> position).

    `pm` is features.match_frame (detailed per-match data); `frame` is features.player_gw_frame
    (official FPL history, used for cards, finishing and the exact DefCon counts).

    `fpl_xg_fallback` (experimental, off by default) lets FPL's own expected goals/assists stand
    in, per stat, for a player with no usable detailed history. It needs `as_of`: only FPL rows
    ingested by then and kicked off before it count. Off, the result is the baseline.
    """
    tr = translations or Translations(target=target)
    pri = position_priors(pm, target)
    disp = nb_dispersion(pm, positions, target)
    d = pm[pm["minutes"] > 0].copy()
    d["w"] = _recency(d["season"], current_season)
    d["fx_share"] = [tr.factor(c, "xg_share") for c in d["comp"]]
    d["fx_xa"] = [tr.factor(c, "xa_share") for c in d["comp"]]
    d["fx_cbit"] = [tr.factor(c, "cbit") for c in d["comp"]]
    d["fx_cbirt"] = [tr.factor(c, "cbirt") for c in d["comp"]]
    d["expo"] = d["team_xg"] * d["minutes"] / 90.0
    d["ew"] = d["w"] * d["expo"]
    d["xa_ew"] = d["ew"].where(d["xa"].notna() & d["expo"].notna(), 0.0)
    d["xg_w"] = d["w"] * d["xg"].fillna(0) * d["fx_share"]
    d["xa_w"] = d["w"] * d["xa"].fillna(0) * d["fx_xa"]
    d["has_xg"] = d["xg"].notna() & d["expo"].notna()
    d.loc[~d["has_xg"], ["ew", "xg_w"]] = 0.0
    d.loc[d["expo"].isna(), "xa_w"] = 0.0
    d["cb_w"] = d["w"] * np.where(d["player_id"].map(positions) == "DEF",
                                  d["cbit"].fillna(0) * d["fx_cbit"],
                                  d["cbirt"].fillna(0) * d["fx_cbirt"])  # fmt: skip
    d["cb_min_w"] = (
        d["w"]
        * d["minutes"]
        * np.where(
            np.where(d["player_id"].map(positions) == "DEF", d["cbit"], d["cbirt"]) >= 0, 1.0, 0.0
        )
    )
    agg = d.groupby("player_id").agg(
        ew=("ew", "sum"), xa_ew=("xa_ew", "sum"),
        xg_w=("xg_w", "sum"), xa_w=("xa_w", "sum"), cb_w=("cb_w", "sum"),
        cb_min_w=("cb_min_w", "sum"), minutes=("minutes", "sum"),
        pl_minutes=("minutes", lambda s: s[d.loc[s.index, "comp"] == target].sum()),
        goals=("goals", "sum"), xg_raw=("xg", "sum"),
    )  # fmt: skip

    # FPL's expected goals/assists are a per-fixture fallback only. They are usable only
    # when the observation timestamp is known and no later than the decision cutoff. Convert
    # the FPL totals to a team-xG share using observed PL team exposure; if that denominator is
    # unavailable, fail closed. Keep xG and xA exposure independent to avoid cross-stat masking.
    fpl = pd.DataFrame()
    if fpl_xg_fallback and as_of is not None and not frame.empty and "stats_ingested_at" in frame:
        cutoff = pd.Timestamp(as_of)
        observed = frame.copy()
        observed["stats_ingested_at"] = pd.to_datetime(
            observed["stats_ingested_at"], errors="coerce"
        )
        observed = observed[
            observed["stats_ingested_at"].notna()
            & (observed["stats_ingested_at"] <= cutoff)
            & (observed["season"] <= current_season)
            & (observed["minutes"] > 0)
        ]
        if "kickoff" in observed:
            observed = observed[
                observed["kickoff"].isna() | (pd.to_datetime(observed["kickoff"]) < cutoff)
            ]
        exposure_rows = pm[(pm["comp"] == target) & (pm["team_xg"] > 0)]
        if "date" in exposure_rows:
            exposure_rows = exposure_rows[pd.to_datetime(exposure_rows["date"]) < cutoff]
        # Multiple player rows share a team's exposure in a match; count each team-match once.
        team_exposure = exposure_rows.drop_duplicates(["match_id", "team_id"])["team_xg"]
        mean_team_xg = float(team_exposure.mean()) if not team_exposure.empty else 0.0
        if mean_team_xg > 0:
            observed["w"] = _recency(observed["season"], current_season)
            observed["fexpo"] = observed["w"] * mean_team_xg * observed["minutes"] / 90.0
            for stat, value_col, expo_col in (
                ("xg", "fpl_xg_w", "fpl_xg_expo"),
                ("xa", "fpl_xa_w", "fpl_xa_expo"),
            ):
                values = pd.to_numeric(observed[stat], errors="coerce")
                usable = values.notna() & (values >= 0)
                observed[expo_col] = observed["fexpo"].where(usable, 0.0)
                observed[value_col] = (values * observed["w"]).where(usable, 0.0)
            fpl = observed.groupby("player_id")[
                ["fpl_xg_w", "fpl_xg_expo", "fpl_xa_w", "fpl_xa_expo"]
            ].sum()

    rows = []
    cards = _cards(frame, positions)
    for pid, pos in positions.items():
        a = agg.loc[pid] if pid in agg.index else None
        b = fpl.loc[pid] if pid in fpl.index else None
        base_share = pri["share"].get(pos, 0.05)
        base_xa = pri.get("xa_share", {}).get(pos, 0.8 * base_share)
        base_cb = pri["cbit"].get(pos, 5.0)
        xg_source = xa_source = "prior"
        if a is None or a["minutes"] <= 0:
            xg_s, xa_s = base_share, base_xa
            finish, cb_mu, evid, src = 1.0, base_cb, 0.0, "prior"
        else:
            xg_s = (a["xg_w"] + EXPOSURE_PRIOR * base_share) / (a["ew"] + EXPOSURE_PRIOR)
            xa_s = (a["xa_w"] + EXPOSURE_PRIOR * base_xa) / (a["xa_ew"] + EXPOSURE_PRIOR)
            finish = float(
                np.clip((a["goals"] + FINISH_PRIOR_XG) / (a["xg_raw"] + FINISH_PRIOR_XG), 0.8, 1.3)
            )
            cb_mu = (a["cb_w"] + CBIT_PRIOR_MINUTES / 90 * base_cb) / (
                a["cb_min_w"] / 90 + CBIT_PRIOR_MINUTES / 90
            )
            evid = float(a["minutes"])
            src = "pl" if a["pl_minutes"] >= 0.5 * a["minutes"] else "translated"
            xg_source = "detailed" if a["ew"] > 0 else "prior"
            xa_source = "detailed" if a["xa_ew"] > 0 else "prior"
        if b is not None:
            if a is None or a["ew"] <= 0:
                expo = float(b["fpl_xg_expo"])
                if expo > 0:
                    xg_s = (float(b["fpl_xg_w"]) + EXPOSURE_PRIOR * base_share) / (
                        expo + EXPOSURE_PRIOR
                    )
                    xg_source = "fpl"
            if a is None or a["xa_ew"] <= 0:
                expo = float(b["fpl_xa_expo"])
                if expo > 0:
                    xa_s = (float(b["fpl_xa_w"]) + EXPOSURE_PRIOR * base_xa) / (
                        expo + EXPOSURE_PRIOR
                    )
                    xa_source = "fpl"
            if "fpl" in (xg_source, xa_source):
                src = "fpl"
        y90, r90 = cards.get(pid, (np.nan, np.nan))
        rows.append(
            {"player_id": pid, "position": pos, "xg_share": float(xg_s), "xa_share": float(xa_s),
             "finish": finish, "cbit_mu": float(cb_mu), "cbit_r": disp.get(pos, 30.0),
             "yellow90": y90, "red90": r90, "evidence_minutes": evid, "source": src,
             "xg_source": xg_source, "xa_source": xa_source}
        )  # fmt: skip
    out = pd.DataFrame(rows, columns=FRAME_COLUMNS)
    # discipline defaults to the position mean where a player has no official history
    for col in ("yellow90", "red90"):
        out[col] = (
            out[col]
            .fillna(out.groupby("position")[col].transform("mean"))
            .fillna({"yellow90": 0.12, "red90": 0.006}[col])
        )
    return out


def _cards(frame: pd.DataFrame, positions: dict[int, str]) -> dict[int, tuple[float, float]]:
    """Yellow and red cards per 90, shrunk to the position mean (official FPL history)."""
    f = frame[frame["minutes"] > 0].copy()
    if f.empty:
        return {}
    f["pos"] = f["player_id"].map(positions)
    mean = f.groupby("pos")[["yellow", "red", "minutes"]].sum()
    pos_y = (mean["yellow"] / mean["minutes"] * 90).to_dict()
    pos_r = (mean["red"] / mean["minutes"] * 90).to_dict()
    g = f.groupby("player_id")[["yellow", "red", "minutes"]].sum()
    k = CARD_PRIOR_MINUTES
    out = {}
    for pid, r in g.iterrows():
        pos = positions.get(pid)
        py, pr = pos_y.get(pos, 0.12), pos_r.get(pos, 0.006)
        out[pid] = ((r["yellow"] + k / 90 * py) / (r["minutes"] / 90 + k / 90),
                    (r["red"] + k / 90 * pr) / (r["minutes"] / 90 + k / 90))  # fmt: skip
    return out
