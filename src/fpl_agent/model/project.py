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

from fpl_agent.db import player_links
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
    inputs: dict[str, int] = field(default_factory=dict)  # degraded-history counts (F-17)

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


# Set-piece duty from FPL's own `penalties_order` / `corners_and_indirect_freekicks_order`.
# A team wins about 0.13 penalties a match, ~78% scored: ~0.10 goals, about 7% of its goals.
# The fitted xG share already holds the penalties a player took before (the sources' xG
# includes penalty xG), so adding a penalty share on top counts an established taker's
# penalties twice. Until the model separates non-penalty xG (npxG is stored by Understat and
# FotMob) and a backtest validates a credit, the extra credit is off (PENALTY_CREDIT = 0); the
# first-choice corner taker keeps a small assist share.
PENALTY_SHARE = 0.07
PENALTY_CREDIT = 0.0
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
        if PENALTY_CREDIT and _order(r.raw, "penalties_order") == 1:
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


def market_lambdas(db: FootballDB, match_id: int, cutoff: datetime | None = None,
                   known_at: bool = False) -> tuple[float, float] | None:  # fmt: skip
    """Goal rates implied by a 1X2 (and totals) price for a match.

    Without `cutoff`, the most recent price held (closing preferred). With a decision
    `cutoff`, closing prices are never used. The newest price observed by the cutoff comes
    from the append-only log; failing that, `known_at=False` (a backtest's event-time
    reconstruction) may use a stored pre-match price whose observation time is unknown, while
    `known_at=True` uses nothing."""
    ok = "home > 1 AND draw > 1 AND away > 1"
    if cutoff is None:
        sql, params = (f"SELECT home, draw, away, over25, under25 FROM match_odds WHERE "
                       f"match_id = ? AND {ok} ORDER BY closing DESC, ingested_at DESC LIMIT 1",
                       [match_id])  # fmt: skip
    else:
        row = db.con.execute(
            f"SELECT home, draw, away, over25, under25 FROM match_odds_log WHERE match_id = ? "
            f"AND NOT closing AND ingested_at <= ? AND {ok} ORDER BY ingested_at DESC LIMIT 1",
            [match_id, cutoff],
        ).fetchone()
        logged = db.con.execute(
            "SELECT 1 FROM match_odds_log WHERE match_id = ? LIMIT 1", [match_id]
        ).fetchone()
        if row or known_at or logged:  # a logged match is judged by its log alone
            return _implied(row)
        sql, params = (f"SELECT home, draw, away, over25, under25 FROM match_odds WHERE "
                       f"match_id = ? AND NOT closing AND {ok} ORDER BY ingested_at DESC LIMIT 1",
                       [match_id])  # fmt: skip
    return _implied(db.con.execute(sql, params).fetchone())


def _implied(row) -> tuple[float, float] | None:
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


# FPL's deadline is normally 90 minutes before a gameweek's first kickoff. Historical deadlines
# are not stored, so backtests and replays cut there: an approximation, reported as such.
DEADLINE_LEAD_MIN = 90


def approx_deadline(first_kickoff) -> datetime:
    """Naive-UTC decision cutoff for a past gameweek from its first kickoff."""
    from datetime import timedelta

    return pd.Timestamp(first_kickoff).to_pydatetime() - timedelta(minutes=DEADLINE_LEAD_MIN)


# ---- the pipeline ----------------------------------------------------------------------------
def history_as_of(
    frame: pd.DataFrame, db: FootballDB, season: int, gw: int, cutoff: datetime | None
) -> pd.DataFrame:
    """The history a decision for `gw` of `season` may see: earlier gameweeks only and, with a
    `cutoff`, only fixtures kicked off before it (a postponed fixture played later is left out)
    with team, position, price and status re-selected from snapshots taken by then. Every fit
    and baseline of an evaluation uses this, not the whole-database frame."""
    history = frame[(frame["season"] < season) | ((frame["season"] == season) & (frame["gw"] < gw))]
    if cutoff is not None and "kickoff" in history:
        history = history[history["kickoff"].isna() | (history["kickoff"] < pd.Timestamp(cutoff))]
    if cutoff is not None:
        history = features.snapshot_as_of(history, db, cutoff)
    return history


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
    known_at_odds: bool = False,
) -> Projection:
    """Projections for `gws`. `cutoff` is the decision time (naive UTC): history, matches,
    player snapshot, odds and league translations are limited to what existed by then, so
    nothing after it can change the result; closing odds are never used with a cutoff.
    `as_of` (a date) defaults to the cutoff's date, else today."""
    cfg = cfg or SimConfig()
    rules = rules or Rules.nearest(season)
    first = gws[0]
    as_of = as_of or (cutoff.date() if cutoff else date.today())
    frame = frame if frame is not None else features.player_gw_frame(db)
    history = history_as_of(frame, db, season, first, cutoff)
    pool = (pool if pool is not None
            else current_pool(db, season, pool_gw or first, as_of=cutoff))  # fmt: skip
    fx = fixtures_for(db, season, gws)
    pool = pool[pool["team_id"].isin(set(fx["home"]) | set(fx["away"]))].reset_index(drop=True)

    if ratings is None:
        ratings = strength.fit_ratings(strength.load_matches(db, as_of=as_of), as_of)
    if minutes_model is None:
        minutes_model = minutes.MinutesModel.fit(history)
    if pm is None:
        pm = features.match_frame(db)
    # an evidence link counts only if it was written and supported by the decision time ...
    decided = cutoff or datetime.combine(as_of, datetime.min.time())
    # ... and only while the evidence stored by then still supports it
    blocked = player_links.unsupported(db, season, decided)
    pm = player_links.resolve(pm[pm["date"] < pd.Timestamp(as_of)], as_of, decided, blocked)
    positions = dict(zip(pool["player_id"], pool["position"], strict=True))
    rates = players.fit_player_rates(
        pm, history, positions, season, translation.Translations.load(db, as_of=cutoff)
    ).set_index("player_id")
    rates = apply_set_piece_duty(rates, pool)
    pool = pool.assign(
        rate_source=pool["player_id"].map(rates["source"]),
        evidence_minutes=pool["player_id"].map(rates["evidence_minutes"]),
    )
    cfg = SimConfig(**{**cfg.__dict__, "noise": fit_bps_noise(history, rules, cfg.bps_save)})

    # minutes probabilities per (player, gw)
    # each player's own first kickoff of the gameweek (not the gameweek's earliest)
    sides = pd.concat([fx[["gw", "home", "kickoff"]].rename(columns={"home": "team"}),
                       fx[["gw", "away", "kickoff"]].rename(columns={"away": "team"})])  # fmt: skip
    kick = sides.groupby(["team", "gw"])["kickoff"].min().to_dict()
    team_gws = set(kick)
    rows = []
    for gw in gws:
        sel = pool[[(t, gw) in team_gws for t in pool["team_id"]]]
        own_kick = [kick.get((t, gw), pd.NaT) for t in sel["team_id"]]
        rows.append(sel.assign(season=season, gw=gw, horizon=gw - first + 1, kickoff=own_kick))
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
        mk = market_lambdas(db, fxr.match_id, cutoff, known_at_odds)
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

    meta = pool[
        [
            "player_id",
            "position",
            "team_id",
            "price",
            "status",
            "chance",
            "rate_source",
            "evidence_minutes",
        ]
    ].copy()
    meta["owned"] = pool.get("owned", pd.Series(float("nan"), index=pool.index))
    return Projection(uuid.uuid4().hex[:12], season, list(gws), meta, points, mins_out,
                      comp_sum, fixtures_out, history_inputs(history))  # fmt: skip


def history_inputs(history: pd.DataFrame) -> dict[str, int]:
    """How much of the official history the models trained on lacks its own snapshot: such rows
    have no price, status or team (the minutes model then drops its price features), and a
    position only where the season's snapshots agree on one."""
    if history.empty or "position_source" not in history:
        return {}
    src = history["position_source"]
    return {"rows": len(history), "own_snapshot": int(src.eq("snapshot").sum()),
            "season_position": int(src.eq("season").sum()), "no_position": int(src.isna().sum()),
            "no_price": int(history["price"].isna().sum()),
            "reconstructed_snapshot": int(history.get("snapshot_kind", src).eq("reconstructed")
                                          .sum())}  # fmt: skip


def save_projection(db: FootballDB, proj: Projection, model: str = MODEL) -> int:
    """Persist per-player-gameweek expectations (the agent's own history lives in the database)."""
    f = proj.frame()
    stamp = now()
    detail = f[["p_zero", "p10", "q10", "q90", "exp_minutes"]].round(4).to_dict("records")
    for row, d in zip(f.to_dict("records"), detail, strict=True):
        if "rate_source" in row:
            d["rate_source"] = row["rate_source"]
            d["evidence_minutes"] = row["evidence_minutes"]
    for name, arr in proj.components.items():  # per-component expectations, same row order
        col = np.concatenate([arr[:, gi] for gi in range(len(proj.gws))])
        for d, v in zip(detail, col, strict=True):
            d.setdefault("components", {})[name] = round(float(v), 4)
    out = pd.DataFrame({
        "run_id": proj.run_id, "model": model, "created_at": stamp, "season": proj.season,
        "gw": f["gw"], "player_id": f["player_id"], "xpts": f["xpts"],
        "detail": [json.dumps(d) for d in detail],
    })  # fmt: skip
    return db.append("projections", out)


def load_projection(db: FootballDB, run_id: str) -> pd.DataFrame:
    return db.query("SELECT * FROM projections WHERE run_id = ?", [run_id])
