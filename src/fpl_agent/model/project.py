"""Projection pipeline: database -> expected-points distributions per player per gameweek.

Fits (or accepts) every model, builds the fixtures of the horizon with goal rates from the team
model (blended with bookmaker odds where there are any), asks the minutes and player models for
each squad member, simulates each fixture and sums doubles; blanks score zero.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from datetime import date, datetime

import numpy as np
import pandas as pd

from fpl_agent.db.database import FootballDB, now
from fpl_agent.model import features, minutes, odds, players, strength, translation
from fpl_agent.model.simulate import BpsNoise, PlayerSpec, SimConfig, simulate_fixture
from fpl_agent.rules import Rules

MODEL = "sim-v1"
COMPETITION = "ENG1"


@dataclass
class Projection:
    run_id: str
    season: int
    gws: list[int]
    meta: pd.DataFrame  # one row per player: player_id, position, team_id, price, status, chance
    points: np.ndarray  # (players, gws, samples) float32
    minutes: np.ndarray  # (players, gws, samples) int16
    components: dict[str, np.ndarray] = field(default_factory=dict)  # name -> (players, gws)
    fixtures: dict[int, list[tuple[int, int, bool, float, float]]] = field(default_factory=dict)

    @property
    def player_ids(self) -> list[int]:
        return self.meta["player_id"].tolist()

    def row(self, player_id: int) -> int:
        return self.player_ids.index(player_id)

    def mean(self) -> np.ndarray:
        return self.points.mean(axis=2)

    def frame(self) -> pd.DataFrame:
        """Long table: player, gw, expected points, risk and upside."""
        m = self.mean()
        p0 = (self.points <= 0).mean(axis=2)
        p10 = (self.points >= 10).mean(axis=2)
        q10, q90 = np.quantile(self.points, [0.1, 0.9], axis=2)
        mins = self.minutes.mean(axis=2)
        rows = []
        for gi, gw in enumerate(self.gws):
            rows.append(pd.DataFrame({
                "player_id": self.player_ids, "gw": gw, "xpts": m[:, gi], "p_zero": p0[:, gi],
                "p10": p10[:, gi], "q10": q10[:, gi], "q90": q90[:, gi], "exp_minutes": mins[:, gi],
            }))  # fmt: skip
        return pd.concat(rows, ignore_index=True).merge(self.meta, on="player_id")


# ---- inputs from the database ------------------------------------------------------------------
def current_pool(db: FootballDB, season: int, gw: int, as_of: datetime | None = None) -> pd.DataFrame:
    """Latest FPL snapshot at or before `as_of` for the gameweek (or the newest one)."""
    sql = (
        "SELECT player_id, team_id, position, now_cost AS price, status, chance_next AS chance, "
        "news, fpl_id, raw FROM fpl_players_gw WHERE season = ? AND gw = ?"
    )
    params: list = [season, gw]
    if as_of is not None:
        sql += " AND snapshot_at <= ?"
        params.append(as_of)
    sql += " QUALIFY snapshot_at = max(snapshot_at) OVER (PARTITION BY fpl_id)"
    df = db.query(sql, params)
    return df.drop_duplicates("player_id").reset_index(drop=True)


def fixtures_for(db: FootballDB, season: int, gws: list[int], comp: str = COMPETITION) -> pd.DataFrame:
    marks = ", ".join("?" for _ in gws)
    return db.query(
        "SELECT match_id, matchday AS gw, home_team_id AS home, away_team_id AS away, kickoff "
        f"FROM matches WHERE competition_id = ? AND season = ? AND matchday IN ({marks}) "
        "ORDER BY matchday, kickoff",
        [comp, season, *gws],
    )


def market_lambdas(db: FootballDB, match_id: int) -> tuple[float, float] | None:
    """Goal rates implied by the most recent 1X2 (and totals) price we hold for a match."""
    row = db.con.execute(
        "SELECT home, draw, away, over25, under25 FROM match_odds WHERE match_id = ? AND home > 1 "
        "AND draw > 1 AND away > 1 ORDER BY closing DESC, ingested_at DESC LIMIT 1",
        [match_id],
    ).fetchone()
    if not row:
        return None
    try:
        return odds.implied_lambdas(*row)
    except ValueError:
        return None


def fit_bps_noise(frame: pd.DataFrame, rules: Rules, save_bps: float = 2.0) -> BpsNoise:
    """BPS not explained by the modelled events, per 90, by position (from official history)."""
    f = frame[(frame["minutes"] > 0) & frame["bps"].notna()].copy()
    if len(f) < 500:
        return BpsNoise()
    bp = rules["bps"]
    pos = f["position"]

    def by(table):
        return pos.map(table).astype(float)

    explained = (
        np.where(f["minutes"] >= 60, bp["minutes_60_plus"], bp["minutes_1_59"])
        + f["goals"].fillna(0) * by(bp["goal"]) + f["assists"].fillna(0) * bp["assist"]
        + f["clean_sheet"].fillna(0) * by(bp["clean_sheet"])
        + f["conceded"].fillna(0) * by(bp["goals_conceded"]) + f["saves"].fillna(0) * save_bps
        + f["pen_saved"].fillna(0) * bp["penalty_saved"] + f["pen_missed"].fillna(0) * bp["penalty_missed"]
        + f["own_goals"].fillna(0) * bp["own_goal"] + f["yellow"].fillna(0) * bp["yellow_card"]
        + f["red"].fillna(0) * bp["red_card"]
    )  # fmt: skip
    f["resid"] = f["bps"] - explained
    f["frac"] = f["minutes"] / 90.0
    mu, sd = {}, {}
    for p, g in f.groupby("position"):
        if len(g) < 100:
            continue
        mu[p] = float((g["resid"] / g["frac"]).clip(-20, 40).mean())
        sd[p] = float((g["resid"] / np.sqrt(g["frac"])).std())
    base = BpsNoise()
    return BpsNoise({**base.mu90, **mu}, {**base.sd90, **sd})


# ---- the pipeline ----------------------------------------------------------------------------
def build_projection(
    db: FootballDB,
    season: int,
    gws: list[int],
    as_of: date | None = None,
    cfg: SimConfig | None = None,
    rules: Rules | None = None,
    minutes_model: minutes.MinutesModel | None = None,
    ratings: strength.TeamRatings | None = None,
    pool: pd.DataFrame | None = None,
    frame: pd.DataFrame | None = None,
    pm: pd.DataFrame | None = None,
    odds_weight: float = 0.5,
    pool_gw: int | None = None,
) -> Projection:
    cfg = cfg or SimConfig()
    rules = rules or Rules.nearest(season)
    first = gws[0]
    as_of = as_of or date.today()
    frame = frame if frame is not None else features.player_gw_frame(db)
    history = frame[(frame["season"] < season) | ((frame["season"] == season) & (frame["gw"] < first))]
    pool = pool if pool is not None else current_pool(db, season, pool_gw or first)
    fx = fixtures_for(db, season, gws)
    pool = pool[pool["team_id"].isin(set(fx["home"]) | set(fx["away"]))].reset_index(drop=True)

    if ratings is None:
        ratings = strength.fit_ratings(strength.load_matches(db, as_of=as_of), as_of)
    if minutes_model is None:
        minutes_model = minutes.MinutesModel.fit(history)
    if pm is None:
        pm = features.match_frame(db)
    pm = pm[pm["date"] < pd.Timestamp(as_of)]
    positions = dict(zip(pool["player_id"], pool["position"], strict=True))
    rates = players.fit_player_rates(
        pm, history, positions, season, translation.Translations.load(db)
    ).set_index("player_id")
    cfg = SimConfig(**{**cfg.__dict__, "noise": fit_bps_noise(history, rules, cfg.bps_save)})

    # minutes probabilities per (player, gw)
    kick = fx.groupby(["gw"])["kickoff"].min()
    team_gws = {(r.home, r.gw) for r in fx.itertuples()} | {(r.away, r.gw) for r in fx.itertuples()}
    rows = []
    for gw in gws:
        sel = pool[[(t, gw) in team_gws for t in pool["team_id"]]]
        rows.append(sel.assign(season=season, gw=gw, horizon=gw - first + 1,
                               kickoff=kick.get(gw, pd.NaT)))  # fmt: skip
    upcoming = pd.concat(rows, ignore_index=True) if rows else pool.iloc[0:0]
    mins = minutes_model.forecast(history, upcoming).set_index(["player_id", "gw"])

    p_idx = {pid: i for i, pid in enumerate(pool["player_id"])}
    g_idx = {gw: i for i, gw in enumerate(gws)}
    n = cfg.n
    points = np.zeros((len(pool), len(gws), n), dtype=np.float32)
    mins_out = np.zeros((len(pool), len(gws), n), dtype=np.int16)
    comp_sum: dict[str, np.ndarray] = {}
    fixtures_out: dict[int, list] = {}
    by_team = {t: g for t, g in pool.groupby("team_id")}

    for fxr in fx.itertuples():
        lam = ratings.lambdas(fxr.home, fxr.away, COMPETITION)
        mk = market_lambdas(db, fxr.match_id)
        lh, la = odds.blend(lam, mk, odds_weight)
        specs = []
        for team in (fxr.home, fxr.away):
            side = []
            for pl in by_team.get(team, pool.iloc[0:0]).itertuples():
                key = (pl.player_id, fxr.gw)
                if key not in mins.index or pl.player_id not in rates.index:
                    continue
                m, r = mins.loc[key], rates.loc[pl.player_id]
                if m.p_start < 0.003 and m.p_cameo < 0.01 and pl.position != "GKP":
                    continue
                side.append(PlayerSpec(
                    pl.player_id, pl.position, float(m.p_start), float(m.p_long), float(m.p_cameo),
                    float(m.cameo_minutes), float(r.xg_share), float(r.xa_share), float(r.finish),
                    float(r.cbit_mu), float(r.cbit_r), float(r.yellow90), float(r.red90),
                ))  # fmt: skip
            specs.append(side)
        if not specs[0] or not specs[1]:
            continue
        rng = np.random.default_rng([cfg.seed, int(fxr.match_id)])
        sim = simulate_fixture(specs[0], specs[1], lh, la, rules, cfg, rng)
        gi = g_idx[fxr.gw]
        for j, pid in enumerate(sim.player_ids):
            i = p_idx[pid]
            points[i, gi] += sim.points[:, j]
            mins_out[i, gi] += sim.minutes[:, j]
            for c, v in sim.components.items():
                comp_sum.setdefault(c, np.zeros((len(pool), len(gws))))[i, gi] += v[j]
        fixtures_out.setdefault(fxr.gw, []).append((fxr.home, fxr.away, True, lh, la))

    meta = pool[["player_id", "position", "team_id", "price", "status", "chance"]].copy()
    return Projection(uuid.uuid4().hex[:12], season, list(gws), meta, points, mins_out,
                      comp_sum, fixtures_out)  # fmt: skip


def save_projection(db: FootballDB, proj: Projection, model: str = MODEL) -> int:
    """Persist per-player-gameweek expectations (the agent's own history lives in the database)."""
    f = proj.frame()
    stamp = now()
    detail = f[["p_zero", "p10", "q10", "q90", "exp_minutes"]].round(4).to_dict("records")
    out = pd.DataFrame({
        "run_id": proj.run_id, "model": model, "created_at": stamp, "season": proj.season,
        "gw": f["gw"], "player_id": f["player_id"], "xpts": f["xpts"],
        "detail": [json.dumps(d) for d in detail],
    })  # fmt: skip
    return db.append("projections", out)


def load_projection(db: FootballDB, run_id: str) -> pd.DataFrame:
    return db.query("SELECT * FROM projections WHERE run_id = ?", [run_id])
