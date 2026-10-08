"""L5 match simulator: per-player distributions of FPL points, fixture by fixture.

For each fixture the simulator draws `n` joint outcomes of the whole match: both teams' goals
(from L2, with a shared per-team intensity so goals are overdispersed), who plays and for how long
(L3), who scores and assists (L4 shares), clean sheets and goals conceded while on the pitch, GK
saves, defensive-contribution counts, cards, then BPS for all 22+ players and the 3-2-1 bonus from
their ranking. Everything is scored with the rules file, so a rule change needs no code change.

Because every player's points come from the same simulated world, the output keeps what a mean
hides: a goalkeeper and two defenders on one team share a clean sheet, an attacker and his
midfielder share a goal. The planner's sensitivity runs and captaincy upside use those samples.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from fpl_agent.rules import Rules

POS_INDEX = {"GKP": 0, "DEF": 1, "MID": 2, "FWD": 3}


@dataclass(frozen=True)
class BpsNoise:
    """BPS earned by things the simulator does not model (tackles, passes, shots...)."""

    mu90: dict[str, float] = field(
        default_factory=lambda: {"GKP": 7.0, "DEF": 9.0, "MID": 8.0, "FWD": 6.0}
    )
    sd90: dict[str, float] = field(
        default_factory=lambda: {"GKP": 5.0, "DEF": 6.5, "MID": 6.5, "FWD": 6.0}
    )


@dataclass(frozen=True)
class SimConfig:
    n: int = 2000
    dispersion: float = 25.0  # gamma shape of the shared team intensity (higher = closer to Poisson)
    sot_conversion: float = 0.33  # goals per shot on target, sets how many saves a GK makes
    assist_prob: float = 0.75
    cbit_opp_exponent: float = 0.25  # more opposition attack -> more defensive actions
    avg_team_goals: float = 1.4
    bps_save: float = 2.0  # BPS per GK save; the 2026/27 value is unconfirmed in the rules file
    keep_events: bool = False  # keep raw event counts (used to verify scoring against the rules)
    pen_save_90: float = 0.012
    pen_miss_90: float = 0.006
    own_goal_90: float = 0.0035
    noise: BpsNoise = field(default_factory=BpsNoise)
    seed: int = 0


@dataclass
class PlayerSpec:
    player_id: int
    position: str
    p_start: float
    p_long: float
    p_cameo: float
    cameo_minutes: float = 20.0
    xg_share: float = 0.05
    xa_share: float = 0.04
    finish: float = 1.0
    cbit_mu: float = 5.0
    cbit_r: float = 30.0
    yellow90: float = 0.12
    red90: float = 0.006


@dataclass
class FixtureSim:
    player_ids: list[int]
    points: np.ndarray  # (n, players) float32
    minutes: np.ndarray  # (n, players) int16
    components: dict[str, np.ndarray]  # mean points per component, each (players,)
    goals_for: dict[str, np.ndarray]  # "home"/"away" goals per sample
    events: dict[str, np.ndarray] | None = None


def _minutes(rng, specs: list[PlayerSpec], n: int, cfg: SimConfig) -> np.ndarray:
    p = len(specs)
    p_start = np.array([s.p_start for s in specs])
    is_gk = np.array([s.position == "GKP" for s in specs])
    # no more than 11 starters: scale the outfield probabilities down if they sum above 10
    out_sum = p_start[~is_gk].sum()
    if out_sum > 10:
        p_start = np.where(is_gk, p_start, p_start * 10 / out_sum)
    start = rng.random((n, p)) < p_start
    if is_gk.any():  # exactly one goalkeeper starts, drawn among them
        gk_p = p_start[is_gk]
        gk_p = gk_p / gk_p.sum() if gk_p.sum() > 0 else np.full(gk_p.shape, 1 / len(gk_p))
        pick = rng.choice(is_gk.nonzero()[0], size=n, p=gk_p)
        start[:, is_gk] = False
        start[np.arange(n), pick] = True
    long = rng.random((n, p)) < np.array([s.p_long for s in specs])
    full = rng.random((n, p)) < 0.72
    sub_off = rng.integers(60, 90, size=(n, p))
    short = rng.integers(15, 60, size=(n, p))
    started_min = np.where(long, np.where(full, 90, sub_off), short)
    cam_mean = np.array([s.cameo_minutes for s in specs])
    cam_min = np.clip(np.rint(rng.exponential(cam_mean, size=(n, p))), 1, 45).astype(int)
    cameo = rng.random((n, p)) < np.array([s.p_cameo for s in specs])
    minutes = np.where(start, started_min, np.where(cameo, cam_min, 0))
    return minutes.astype(np.int16)


def _allocate(rng, weights: np.ndarray, goals: np.ndarray):
    """Hand each goal to a player drawn in proportion to `weights` (n, p). Returns the counts and
    the player index of the k-th goal (-1 where there was none)."""
    n, p = weights.shape
    counts = np.zeros((n, p), dtype=np.int16)
    who = np.full((n, max(int(goals.max()), 1)), -1, dtype=int)
    rows = np.arange(n)
    tot = weights.sum(1)
    cum = np.cumsum(weights, axis=1)
    for k in range(int(goals.max())):
        ok = (goals > k) & (tot > 0)
        u = rng.random(n) * np.where(tot > 0, tot, 1.0)
        idx = np.minimum((cum < u[:, None]).sum(1), p - 1)
        who[ok, k] = idx[ok]
        np.add.at(counts, (rows[ok], idx[ok]), 1)
    return counts, who


def bonus_from_bps(bps: np.ndarray, played: np.ndarray, awards=(3, 2, 1)) -> np.ndarray:
    """3-2-1 bonus per sample from BPS (n, p), ties handled as in FPL: a player's place is the
    number of players with strictly more BPS, and gets the award for that place if there is one."""
    b = np.where(played, bps, -np.inf)
    place = (b[:, None, :] > b[:, :, None]).sum(-1)
    out = np.zeros(bps.shape, dtype=np.int16)
    for i, a in enumerate(awards):
        out = np.where((place == i) & played, a, out)
    return out


def _side(rng, specs, g_for, g_against, lam_against, minutes, cfg, rules):
    """Events and points for one team's players in `n` simulated matches."""
    n, p = minutes.shape
    sc, bp = rules["scoring"], rules["bps"]
    pos = np.array([POS_INDEX[s.position] for s in specs])
    played = minutes > 0
    frac = minutes / 90.0
    share = np.array([s.xg_share * s.finish for s in specs])
    ashare = np.array([s.xa_share for s in specs])
    goals, scorer = _allocate(rng, share * frac, g_for)
    # assists: on a share of goals, never by the scorer
    has_assist = rng.random(scorer.shape) < cfg.assist_prob
    assists = np.zeros((n, p), dtype=np.int16)
    rows = np.arange(n)
    for k in range(scorer.shape[1]):
        act = (scorer[:, k] >= 0) & has_assist[:, k]
        if not act.any():
            continue
        w = ashare * frac
        w = np.broadcast_to(w, (n, p)).copy()
        w[rows[act], scorer[act, k]] = 0.0
        tot = w.sum(1)
        ok = act & (tot > 0)
        cum = np.cumsum(w, axis=1)
        u = rng.random(n) * np.where(tot > 0, tot, 1.0)
        idx = np.minimum((cum < u[:, None]).sum(1), p - 1)
        np.add.at(assists, (rows[ok], idx[ok]), 1)
    conceded = rng.binomial(g_against[:, None], np.clip(minutes, 0, 90) / 90.0).astype(np.int16)
    conceded = np.where(played, conceded, 0)
    cs = (conceded == 0) & (minutes >= 60)
    is_gk = pos == 0
    saves = rng.poisson(lam_against * (1 / cfg.sot_conversion - 1) * frac) * is_gk
    saves = np.where(played, saves, 0)
    # defensive contribution: negative binomial counts, harder when the opponent attacks less
    opp = (lam_against / cfg.avg_team_goals) ** cfg.cbit_opp_exponent
    mu = np.array([s.cbit_mu for s in specs]) * frac * opp
    r = np.array([s.cbit_r for s in specs])
    rate = rng.gamma(r, np.where(mu > 0, mu, 1e-9) / r, size=(n, p))
    count = rng.poisson(rate)
    thr = np.array([sc["defcon"]["threshold"].get(s.position, 10**6) for s in specs])
    defcon = (count >= thr) & played
    yellow = rng.random((n, p)) < np.array([s.yellow90 for s in specs]) * frac
    red = rng.random((n, p)) < np.array([s.red90 for s in specs]) * frac
    og = rng.random((n, p)) < cfg.own_goal_90 * frac
    pen_miss = rng.random((n, p)) < cfg.pen_miss_90 * frac * (share / 0.1) * (~is_gk)
    pen_save = rng.random((n, p)) < cfg.pen_save_90 * frac * is_gk

    def by_pos(table):
        return np.array([table[k] for k in ("GKP", "DEF", "MID", "FWD")])[pos]

    comps = {
        "minutes": np.where(minutes >= 60, sc["minutes_long"], np.where(played, sc["minutes_short"], 0)),
        "goals": goals * by_pos(sc["goal"]),
        "assists": assists * sc["assist"],
        "clean_sheets": cs * by_pos(sc["clean_sheet"]),
        "goals_conceded": (conceded // sc["goals_conceded_per"]) * by_pos(sc["goals_conceded"]),
        "saves": (saves // sc["saves_per"]) * sc["save_points"] * is_gk,
        "pen_saved": pen_save * sc["penalty_saved"],
        "pen_missed": pen_miss * sc["penalty_missed"],
        "cards": yellow * sc["yellow_card"] + red * sc["red_card"],
        "own_goals": og * sc["own_goal"],
        "defcon": defcon * sc["defcon"]["points"] * sc["defcon"]["cap_per_match"],
    }
    # BPS: modelled events + noise for everything else
    noise_mu = np.array([cfg.noise.mu90[s.position] for s in specs]) * frac
    noise_sd = np.array([cfg.noise.sd90[s.position] for s in specs]) * np.sqrt(frac)
    bps = (
        np.where(minutes >= 60, bp["minutes_60_plus"], np.where(played, bp["minutes_1_59"], 0))
        + goals * by_pos(bp["goal"]) + assists * bp["assist"]
        + cs * by_pos(bp["clean_sheet"]) + conceded * by_pos(bp["goals_conceded"])
        + saves * cfg.bps_save + pen_save * bp["penalty_saved"] + pen_miss * bp["penalty_missed"]
        + og * bp["own_goal"] + yellow * bp["yellow_card"] + red * bp["red_card"]
        + rng.normal(noise_mu, np.maximum(noise_sd, 1e-9), size=(n, p))
    )  # fmt: skip
    events = {"minutes": minutes, "goals": goals, "assists": assists, "clean_sheets": cs,
              "conceded": conceded, "saves": saves, "count": count, "yellow": yellow, "red": red,
              "og": og, "pen_missed": pen_miss, "pen_saved": pen_save}  # fmt: skip
    return comps, np.where(played, bps, 0.0), played, events


def simulate_fixture(
    home: list[PlayerSpec],
    away: list[PlayerSpec],
    lam_home: float,
    lam_away: float,
    rules: Rules,
    cfg: SimConfig | None = None,
    rng: np.random.Generator | None = None,
) -> FixtureSim:
    cfg = cfg or SimConfig()
    rng = rng or np.random.default_rng(cfg.seed)
    n = cfg.n
    k = cfg.dispersion
    g_home = rng.poisson(lam_home * rng.gamma(k, 1 / k, size=n))
    g_away = rng.poisson(lam_away * rng.gamma(k, 1 / k, size=n))
    out_comps, bps_all, played_all, minutes_all, events_all = [], [], [], [], []
    for specs, g_for, g_against, lam_against in (
        (home, g_home, g_away, lam_away),
        (away, g_away, g_home, lam_home),
    ):
        minutes = _minutes(rng, specs, n, cfg)
        comps, bps, played, events = _side(
            rng, specs, g_for, g_against, lam_against, minutes, cfg, rules
        )
        events_all.append(events)
        out_comps.append(comps)
        bps_all.append(bps)
        played_all.append(played)
        minutes_all.append(minutes)
    bonus = bonus_from_bps(np.hstack(bps_all), np.hstack(played_all), tuple(rules["bonus_points"]))
    nh = len(home)
    parts = []
    for side, comps in enumerate(out_comps):
        sl = slice(0, nh) if side == 0 else slice(nh, None)
        comps["bonus"] = bonus[:, sl] * rules["scoring"]["bonus"]
        parts.append(comps)
    names = list(parts[0])
    comps = {c: np.hstack([parts[0][c], parts[1][c]]) for c in names}
    points = sum(comps.values()).astype(np.float32)
    return FixtureSim(
        player_ids=[s.player_id for s in home + away],
        points=points,
        minutes=np.hstack(minutes_all),
        components={c: v.mean(0) for c, v in comps.items()},
        goals_for={"home": g_home, "away": g_away},
        events=(
            {k: np.hstack([e[k] for e in events_all]) for k in events_all[0]} | {"bonus": bonus}
            if cfg.keep_events
            else None
        ),
    )
