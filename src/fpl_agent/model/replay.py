"""Season replay: run the real agent through a past season and score it on what happened.

Each gameweek the agent sees only what existed before its deadline (prices and history up to the
previous gameweek, matches before the first kickoff), projects the horizon, and decides exactly as
it would live: the planner, the chip search, the robust-transfer and captain rails. The decision is
then scored with the official points: automatic substitutions, the vice-captain cover, chips and
-4 per hit. The Free Hit squad reverts the week after. Selling prices follow FPL's half-the-rise
rule. This is the scoreboard every later change to the planner is judged on.

History has no ownership, status or news, so the ownership rails and the rank-risk term are off,
and every player is treated as available. The first gameweek's squad is the planner's free pick
with £100m.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace

import pandas as pd

from fpl_agent.data.entry import SquadState
from fpl_agent.db.database import FootballDB
from fpl_agent.model import features, minutes, project, strength
from fpl_agent.model.simulate import SimConfig
from fpl_agent.rules import Rules
from fpl_agent.rules.engine import auto_sub, effective_captain, selling_price
from fpl_agent.solver import chips as chips_mod
from fpl_agent.solver.decide import decide
from fpl_agent.solver.inputs import build_candidates, prune
from fpl_agent.solver.planner import PlanInput, PlannerConfig, SquadPlayer, WeekPlan, solve_plan

BUDGET = 1000  # £100.0m in tenths
HIT = 4


@dataclass(frozen=True)
class ReplayConfig:
    horizon: int = 4
    samples: int = 300
    use_chips: bool = True
    refit_every: int = 6  # minutes model refit, in gameweeks
    planner: PlannerConfig = field(default_factory=lambda: PlannerConfig(time_limit=10.0))
    chips: chips_mod.ChipConfig = field(
        default_factory=lambda: chips_mod.ChipConfig(n_worlds=150, scenario_time_limit=5.0)
    )


@dataclass
class ReplayWeek:
    gw: int
    points: int  # after hits
    hits: int
    chip: str | None
    transfers: int
    captain: int
    captain_points: int
    bench_points: int
    xp: float  # what the plan expected (before hits)


@dataclass
class ReplayResult:
    season: int
    weeks: list[ReplayWeek]

    @property
    def total(self) -> int:
        return sum(w.points for w in self.weeks)

    def frame(self) -> pd.DataFrame:
        return pd.DataFrame([w.__dict__ for w in self.weeks])

    def summary(self) -> str:
        if not self.weeks:
            return "no gameweeks replayed"
        df = self.frame()
        chips = ", ".join(f"{w.chip} GW{w.gw}" for w in self.weeks if w.chip) or "none"
        return (
            f"season {self.season}/{(self.season + 1) % 100:02d}, GW{df.gw.min()} to "
            f"GW{df.gw.max()}: {self.total} points ({self.total / len(df):.1f} a gameweek), "
            f"{int(df.hits.sum())} hits, {int(df.transfers.sum())} transfers, chips: {chips}; "
            f"captains scored {int(df.captain_points.sum())}, bench left "
            f"{int(df.bench_points.sum())}; the plans expected {df.xp.sum():.0f} before hits"
        )


def pool_as_of(db: FootballDB, season: int, gw: int) -> pd.DataFrame:
    """Every player's latest FPL row at or before `gw` (so a player who blanks is still there)."""
    df = db.query(
        "SELECT player_id, team_id, position, now_cost AS price, status, chance_next AS chance, "
        "news, fpl_id, selected_by_percent AS owned, raw, gw FROM fpl_players_gw "
        "WHERE season = ? AND gw <= ? AND player_id IS NOT NULL AND team_id IS NOT NULL "
        "QUALIFY row_number() OVER (PARTITION BY player_id ORDER BY gw DESC, snapshot_at DESC) = 1",
        [season, gw],
    )
    return df.drop(columns="gw").reset_index(drop=True)


def actual_week(db: FootballDB, season: int, gw: int) -> tuple[dict[int, int], dict[int, int]]:
    df = db.query(
        "SELECT player_id, sum(total_points) AS pts, sum(minutes) AS mins FROM fpl_points_gw "
        "WHERE season = ? AND gw = ? AND player_id IS NOT NULL GROUP BY player_id",
        [season, gw],
    )
    ids = df["player_id"].astype(int)
    return (dict(zip(ids, df["pts"].astype(int), strict=True)),
            dict(zip(ids, df["mins"].astype(int), strict=True)))  # fmt: skip


def score_week(wk: WeekPlan, pts: dict[int, int], mins: dict[int, int], pos: dict[int, str],
               rules: Rules) -> tuple[int, int, int]:  # fmt: skip
    """(points before hits, captain's points, bench points left) for one week."""
    final = auto_sub(wk.xi, wk.bench, mins, pos, rules)
    cap = effective_captain(wk.captain, wk.vice, mins)
    total = sum(pts.get(p, 0) for p in final)
    cap_pts = pts.get(cap, 0) if cap is not None else 0
    total += (2 if wk.chip == "3xc" else 1) * cap_pts
    bench = sum(pts.get(p, 0) for p in wk.squad if p not in final)
    if wk.chip == "bboost":
        total, bench = total + bench, 0
    return total, cap_pts, bench


def _first_squad(proj: project.Projection, cfg: PlannerConfig) -> WeekPlan:
    cands = prune(build_candidates(proj, []), [])
    pi = PlanInput(gws=list(proj.gws), squad=[], bank=BUDGET, free_transfers=1,
                   candidates=cands, chips={proj.gws[0]: "wildcard"})  # fmt: skip
    wk = solve_plan(pi, cfg).first
    wk.chip, wk.transfers_in, wk.transfers_out = None, [], []
    return wk


def replay_season(db: FootballDB, season: int, gws: list[int] | None = None,
                  cfg: ReplayConfig | None = None, rules: Rules | None = None,
                  progress=None) -> ReplayResult:  # fmt: skip
    cfg = cfg or ReplayConfig()
    rules = rules or Rules.nearest(season)
    last = int(db.con.execute(
        "SELECT coalesce(max(gw), 0) FROM fpl_points_gw WHERE season = ?", [season]
    ).fetchone()[0])  # fmt: skip
    gws = gws or list(range(1, last + 1))
    frame = features.player_gw_frame(db)
    pm = features.match_frame(db)
    matches = strength.load_matches(db)
    sim = SimConfig(n=cfg.samples)

    squad: list[SquadPlayer] = []
    bank, ft = BUDGET, 1
    chips_used: list[tuple[str, int]] = []
    model, fitted_at = None, None
    out: list[ReplayWeek] = []
    for gw in gws:
        hz = [g for g in range(gw, min(gw + cfg.horizon, last + 1))
              if not project.fixtures_for(db, season, [g]).empty]  # fmt: skip
        if not hz or hz[0] != gw:
            continue
        fx = project.fixtures_for(db, season, [gw])
        as_of = pd.Timestamp(fx["kickoff"].min()).date()
        history = frame[
            (frame["season"] < season) | ((frame["season"] == season) & (frame["gw"] < gw))
        ]
        if model is None or fitted_at is None or gw - fitted_at >= cfg.refit_every:
            model, fitted_at = minutes.MinutesModel.fit(history), gw
        pool = pool_as_of(db, season, gw).assign(status="a", chance=None)
        proj = project.build_projection(
            db, season, hz, as_of=as_of, cfg=sim, rules=rules, minutes_model=model,
            ratings=strength.fit_ratings(matches, as_of), pool=pool, frame=frame, pm=pm,
        )  # fmt: skip
        price = dict(zip(pool["player_id"].astype(int), pool["price"].astype(int), strict=True))
        team = dict(zip(pool["player_id"].astype(int), pool["team_id"].astype(int), strict=True))
        pos = dict(zip(pool["player_id"].astype(int), pool["position"], strict=True))
        if not squad:  # the first week: a free pick with the full budget
            wk = _first_squad(proj, cfg.planner)
            squad = [SquadPlayer(p, pos[p], team[p], price[p], price[p]) for p in wk.squad]
            bank = BUDGET - sum(price[p] for p in wk.squad)
            n_in = 0
        else:
            squad = [replace(p, team_id=team.get(p.player_id, p.team_id),
                             sell_price=selling_price(p.buy_price,
                                                      price.get(p.player_id, p.sell_price)))
                     for p in squad]  # fmt: skip
            state = SquadState(next_gw=gw, squad=squad, bank=bank, free_transfers=ft,
                               chips_used=list(chips_used))  # fmt: skip
            d = decide(state, proj, rules, cfg=cfg.planner, chip_cfg=cfg.chips,
                       use_chips=cfg.use_chips)  # fmt: skip
            wk, n_in = d.week, len(d.transfers_in)
            if wk.chip:
                chips_used.append((wk.chip, gw))
            if wk.chip != "freehit":  # a Free Hit squad is gone the week after
                keep = {p.player_id: p for p in squad}
                squad = [keep.get(p) or SquadPlayer(p, pos[p], team[p], price[p], price[p])
                         for p in wk.squad]  # fmt: skip
                bank = d.bank_after
            if wk.chip in ("wildcard", "freehit"):
                ft = min(cfg.planner.max_free, ft + 1)
            else:
                ft = min(cfg.planner.max_free, ft - min(n_in, ft) + 1)
        pts, mins = actual_week(db, season, gw)
        week_pos = {**{p.player_id: p.position for p in squad}, **pos}
        got, cap_pts, bench = score_week(wk, pts, mins, week_pos, rules)
        res = ReplayWeek(gw, got - HIT * wk.hits, wk.hits, wk.chip, n_in, wk.captain, cap_pts,
                         bench, round(wk.xp, 1))  # fmt: skip
        out.append(res)
        if progress:
            progress(res)
    return ReplayResult(season, out)
