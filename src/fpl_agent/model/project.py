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

from fpl_agent import pit
from fpl_agent.db.database import FootballDB, now
from fpl_agent.model import features, minutes, odds, overlays, players, strength, translation
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
    cutoff: datetime | None = None  # data observed at or before this (naive UTC) only
    variant: str = "primary"  # "primary" (what the plan used) or a shadow variant
    flags: dict = field(default_factory=dict)  # feature modes and what each changed
    limitations: list[str] = field(default_factory=list)  # point-in-time caveats of the inputs
    minutes_table: pd.DataFrame | None = None  # per (player, gw): p_start, p_cameo, overlays
    inputs: dict = field(default_factory=dict, repr=False)  # fitted models, to reuse (not saved)

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
def current_pool(
    db: FootballDB, season: int, gw: int, as_of: datetime | None = None
) -> pd.DataFrame:
    """Latest FPL snapshot at or before `as_of` for the gameweek (or the newest one)."""
    sql = (
        "SELECT player_id, team_id, position, now_cost AS price, status, chance_next AS chance, "
        "news, fpl_id, selected_by_percent AS owned, raw "
        "FROM fpl_players_gw WHERE season = ? AND gw = ?"
    )
    params: list = [season, gw]
    if as_of is not None:
        sql += " AND snapshot_at <= ?"
        params.append(as_of)
    sql += " QUALIFY snapshot_at = max(snapshot_at) OVER (PARTITION BY fpl_id)"
    df = db.query(sql, params)
    return df.drop_duplicates("player_id").reset_index(drop=True)


def backfill_pool(db: FootballDB, season: int, gw: int) -> pd.DataFrame:
    """A historical gameweek's squad rows from an archive backfill (vaastav): stamped at the first
    kickoff, so they are after the deadline and carry no real availability (status is always
    'a'). Usable for backtests only, with that limitation reported."""
    return current_pool(db, season, gw)


# Set-piece duty from FPL's own `penalties_order` / `corners_and_indirect_freekicks_order`.
# A team wins about 0.13 penalties a match, ~78% scored: ~0.10 goals, about 7% of its goals.
# The fitted xG share already holds the penalties a player took before, so the first-choice
# taker gets half of that share on top (a first estimate until the backtest tunes it), and the
# first-choice corner taker a small assist share.
PENALTY_SHARE = 0.07
PENALTY_CREDIT = 0.5
CORNER_ASSIST_SHARE = 0.01


def _order(raw: object, key: str) -> int | None:
    try:
        v = json.loads(raw).get(key) if isinstance(raw, str) else None
    except ValueError:
        return None
    return int(v) if isinstance(v, int | float) else None


def apply_set_piece_duty(rates: pd.DataFrame, pool: pd.DataFrame) -> pd.DataFrame:
    """`rates` (indexed by player_id) with the first-choice penalty and corner takers' shares
    raised. Players without FPL's duty fields are left as they are."""
    if "raw" not in pool:
        return rates
    out = rates.copy()
    for r in pool.itertuples():
        if r.player_id not in out.index:
            continue
        if _order(r.raw, "penalties_order") == 1:
            out.loc[r.player_id, "xg_share"] += PENALTY_SHARE * PENALTY_CREDIT
        if _order(r.raw, "corners_and_indirect_freekicks_order") == 1:
            out.loc[r.player_id, "xa_share"] += CORNER_ASSIST_SHARE
    return out


def fixtures_for(
    db: FootballDB, season: int, gws: list[int], comp: str = COMPETITION
) -> pd.DataFrame:
    marks = ", ".join("?" for _ in gws)
    return db.query(
        "SELECT match_id, matchday AS gw, home_team_id AS home, away_team_id AS away, kickoff "
        f"FROM matches WHERE competition_id = ? AND season = ? AND matchday IN ({marks}) "
        "ORDER BY matchday, kickoff",
        [comp, season, *gws],
    )


def market_lambdas(
    db: FootballDB, match_id: int, as_of: datetime | None = None, allow_backfill: bool = True
) -> tuple[float, float] | None:
    """Goal rates implied by the most recent 1X2 (and totals) price we hold for a match.

    With `as_of`, only prices that existed then are used. A closing price exists from kickoff,
    so it is never used before the match starts. A pre-match price observed at or before
    `as_of` is point-in-time; one loaded later by a historical backfill (its real publication
    time is unknown) is used only when no point-in-time price exists and `allow_backfill`.
    """
    sql = (
        "SELECT o.home, o.draw, o.away, o.over25, o.under25 FROM match_odds o "
        "LEFT JOIN matches m ON m.match_id = o.match_id WHERE o.match_id = ? AND o.home > 1 "
        "AND o.draw > 1 AND o.away > 1"
    )
    params: list = [match_id]
    if as_of is None:
        row = db.con.execute(sql + " ORDER BY o.closing DESC, o.ingested_at DESC LIMIT 1",
                             params).fetchone()  # fmt: skip
    else:
        pre = " AND NOT (o.closing AND (m.kickoff IS NULL OR m.kickoff > ?))"
        row = db.con.execute(
            sql
            + pre
            + " AND o.ingested_at <= ? ORDER BY o.closing DESC, o.ingested_at DESC LIMIT 1",
            [*params, as_of, as_of],
        ).fetchone()
        if row is None and allow_backfill:
            row = db.con.execute(
                sql + pre + " AND NOT o.closing ORDER BY o.ingested_at ASC LIMIT 1",
                [*params, as_of],
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
        + f["pen_saved"].fillna(0) * bp["penalty_saved"]
        + f["pen_missed"].fillna(0) * bp["penalty_missed"]
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
    cutoff: datetime | None = None,
    overlays: list | None = None,
    allow_backfill: bool = True,
) -> Projection:
    """Project `gws`. `cutoff` (naive UTC) is the information cutoff: the squad snapshot, the
    bookmaker prices and the matches the models see must have existed by then. `overlays` are
    bounded minutes adjustments (see `model.overlays`), applied after the minutes model and
    before simulation; each one records what it changed."""
    cfg = cfg or SimConfig()
    rules = rules or Rules.nearest(season)
    first = gws[0]
    as_of = as_of or (cutoff.date() if cutoff is not None else date.today())
    limitations: list[str] = []
    frame = frame if frame is not None else features.player_gw_frame(db, as_of=cutoff)
    history = frame[
        (frame["season"] < season) | ((frame["season"] == season) & (frame["gw"] < first))
    ]
    if pool is None:
        pool = current_pool(db, season, pool_gw or first, as_of=cutoff)
        if pool.empty and cutoff is not None and allow_backfill:
            pool = backfill_pool(db, season, pool_gw or first)
            if not pool.empty:
                limitations.append("squad rows from an archive backfill stamped after the "
                                   "deadline: prices as at kickoff, availability unknown "
                                   "(everyone assumed available)")  # fmt: skip
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
    rates = apply_set_piece_duty(rates, pool)
    cfg = SimConfig(**{**cfg.__dict__, "noise": fit_bps_noise(history, rules, cfg.bps_save)})

    # minutes probabilities per (player, gw). Each club's own kickoff: in a double gameweek its
    # first fixture (both fixtures share the forecast made at the deadline, as in evaluation).
    home = fx[["gw", "home", "kickoff"]].rename(columns={"home": "team_id"})
    away = fx[["gw", "away", "kickoff"]].rename(columns={"away": "team_id"})
    sides = pd.concat([home, away])
    club_ko = sides.groupby(["team_id", "gw"])["kickoff"].min().to_dict()
    rows = []
    for gw in gws:
        sel = pool[[(t, gw) in club_ko for t in pool["team_id"]]]
        rows.append(sel.assign(season=season, gw=gw, horizon=gw - first + 1,
                               kickoff=[club_ko[(t, gw)] for t in sel["team_id"]]))  # fmt: skip
    upcoming = pd.concat(rows, ignore_index=True) if rows else pool.iloc[0:0]
    if "kickoff" in upcoming:
        upcoming["kickoff"] = pd.to_datetime(upcoming["kickoff"])
    mins = minutes_model.forecast(history, upcoming)
    flags: dict = {}
    for ov in overlays or []:
        mins = ov.apply(
            mins, upcoming, cutoff, {"history": history, "minutes_model": minutes_model}
        )
        flags[ov.name] = ov.summary()
    minutes_table = mins.copy()
    mins = mins.set_index(["player_id", "gw"])

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
        mk = market_lambdas(db, fxr.match_id, as_of=cutoff, allow_backfill=allow_backfill)
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
                      comp_sum, fixtures_out, cutoff=cutoff, flags=flags,
                      limitations=limitations, minutes_table=minutes_table,
                      inputs={"ratings": ratings, "minutes_model": minutes_model, "frame": frame,
                              "pm": pm, "pool": pool})  # fmt: skip


MINUTES_DETAIL = ("p_start", "p_cameo", "availability")


def save_projection(
    db: FootballDB,
    proj: Projection,
    model: str = MODEL,
    created_at: datetime | None = None,
    kind: str = "live",
    parent_run_id: str | None = None,
    extra_flags: dict | None = None,
) -> int:
    """Persist per-player-gameweek expectations and register the run (when it was made, the
    data cutoff, the deadline of its first gameweek and the versions behind it), so a review can
    later tell a forecast that was issued before the deadline from one made after it."""
    f = proj.frame()
    stamp = created_at or now()
    detail = f[["p_zero", "p10", "q10", "q90", "exp_minutes"]].round(4).to_dict("records")
    for name, arr in proj.components.items():  # per-component expectations, same row order
        col = np.concatenate([arr[:, gi] for gi in range(len(proj.gws))])
        for d, v in zip(detail, col, strict=True):
            d.setdefault("components", {})[name] = round(float(v), 4)
    if proj.minutes_table is not None and not proj.minutes_table.empty:
        mt = proj.minutes_table.drop_duplicates(["player_id", "gw"]).set_index(["player_id", "gw"])
        for d, pid, gw in zip(detail, f["player_id"], f["gw"], strict=True):
            if (pid, gw) not in mt.index:
                continue
            r = mt.loc[(pid, gw)]
            for k in MINUTES_DETAIL:
                if k in r and pd.notna(r[k]):
                    d[k] = round(float(r[k]), 4)
            for name in overlays.FEATURES:  # what each overlay changed for this player
                v = r.get(name)
                if isinstance(v, list | dict) and v:
                    d[name] = v
    out = pd.DataFrame({
        "run_id": proj.run_id, "model": model, "created_at": stamp, "season": proj.season,
        "gw": f["gw"], "player_id": f["player_id"], "xpts": f["xpts"],
        "detail": [json.dumps(d, default=str) for d in detail], "variant": proj.variant,
    })  # fmt: skip
    n = db.append("projections", out)
    pit.register_run(db, proj.run_id, proj.season, proj.gws, stamp, proj.cutoff, kind=kind,
                     variant=proj.variant, parent_run_id=parent_run_id, model=model,
                     flags={**proj.flags, **(extra_flags or {})},
                     limitations=proj.limitations)  # fmt: skip
    return n


def load_projection(db: FootballDB, run_id: str) -> pd.DataFrame:
    return db.query("SELECT * FROM projections WHERE run_id = ?", [run_id])
