"""Season replay: run the real agent through a past season and score it on what happened.

Each gameweek the agent sees only what existed before its deadline (prices and history up to the
previous gameweek, matches before the first kickoff), projects the horizon, and decides exactly as
it would live: the planner, the chip search, the robust-transfer and captain rails. The decision is
then scored with the official points: automatic substitutions, the vice-captain cover, chips and
-4 per hit. The Free Hit squad reverts the week after. Selling prices follow FPL's half-the-rise
rule. This is the scoreboard every later change to the planner is judged on.

History has no ownership, status or news, so the ownership rails and the rank-risk term are off,
and every player is treated as available (a validated replay keeps the availability of the
observed pre-deadline snapshot). The first gameweek's squad is the planner's free pick
with £100m.
"""

from __future__ import annotations

from dataclasses import dataclass, field, fields, replace
from datetime import datetime

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


def tuned(cfg: ReplayConfig, settings: list[str]) -> ReplayConfig:
    """Override planner and chip settings with `key=value` strings, e.g. `hit_buffer=1.5`,
    `ft_values=2.2,1.6,1.2,1.0`, `min_gain.bboost=12` or `hold_margin=2` (for tuning runs)."""
    planner, chips = cfg.planner, cfg.chips
    known_p = {f.name: f for f in fields(PlannerConfig)}
    known_c = {f.name for f in fields(chips_mod.ChipConfig)}
    for item in settings:
        key, _, raw = item.partition("=")
        key = key.strip()
        if key in known_p:
            planner = replace(planner, **{key: _parse(getattr(planner, key), raw)})
        elif key.split(".")[0] in ("min_gain", "min_win") and "." in key:
            table, chip = key.split(".", 1)
            values = {**getattr(chips, table), chip: float(raw)}
            chips = replace(chips, **{table: values})
        elif key in known_c:
            chips = replace(chips, **{key: _parse(getattr(chips, key), raw)})
        elif key in ("horizon", "samples"):
            cfg = replace(cfg, **{key: int(raw)})
        else:
            raise ValueError(f"unknown setting {key!r}")
    return replace(cfg, planner=planner, chips=chips)


def _parse(current, raw: str):
    if isinstance(current, tuple):
        return tuple(float(x) for x in raw.split(","))
    if isinstance(current, bool):
        return raw.lower() in ("1", "true", "yes")
    return type(current)(float(raw)) if isinstance(current, int) else float(raw)


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
    detail: dict | None = field(default=None, repr=False)  # the plan, moves and projections


@dataclass
class ReplayResult:
    season: int
    weeks: list[ReplayWeek]

    @property
    def total(self) -> int:
        return sum(w.points for w in self.weeks)

    def frame(self) -> pd.DataFrame:
        return pd.DataFrame(
            [{k: v for k, v in w.__dict__.items() if k != "detail"} for w in self.weeks]
        )

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


def pool_as_of(
    db: FootballDB, season: int, gw: int, cutoff: datetime | None = None
) -> pd.DataFrame:
    """Every player's latest FPL row at or before `gw` (so a player who blanks is still there);
    with `cutoff`, only rows snapshotted by then (a late or corrected snapshot is not seen)."""
    df = db.query(
        "SELECT player_id, team_id, position, now_cost AS price, status, chance_next AS chance, "
        "news, fpl_id, selected_by_percent AS owned, raw, gw FROM fpl_players_gw "
        "WHERE season = ? AND gw <= ? AND player_id IS NOT NULL AND team_id IS NOT NULL "
        "AND (CAST(? AS TIMESTAMP) IS NULL OR snapshot_at <= ?) "
        "QUALIFY row_number() OVER (PARTITION BY player_id ORDER BY gw DESC, snapshot_at DESC) = 1",
        [season, gw, cutoff, cutoff],
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
                  progress=None, start: SquadState | None = None,
                  pool_fn=None, cutoff_fn=None,
                  validated: bool = False) -> ReplayResult:  # fmt: skip
    """`start` begins from a real squad (canonical player ids) instead of a free first pick, and
    `pool_fn(db, season, gw)` replaces the default pool (every player available).
    `cutoff_fn(gw)` gives the exact decision time when known; otherwise each week is cut 90
    minutes before its first kickoff (FPL's usual deadline; historical deadlines are not
    stored), and no data, odds or translation fit from after the cut is used.
    `validated`: only eligible gameweeks (else NotEligible), cut at their official deadline,
    excluded matches removed from the model inputs (`db.eligibility.validated_inputs`), the
    pool taken from snapshots observed by the cutoff with FPL's availability kept, and only odds
    observed by the cutoff; decisions are still scored on FPL's official points. Without it the
    replay is exploratory. In both modes every fit sees only the history visible at the
    cutoff."""
    cfg = cfg or ReplayConfig()
    rules = rules or Rules.nearest(season)
    last = int(db.con.execute(
        "SELECT coalesce(max(gw), 0) FROM fpl_points_gw WHERE season = ?", [season]
    ).fetchone()[0])  # fmt: skip
    gws = gws or list(range(1, last + 1))
    frame = features.player_gw_frame(db)
    pm = features.match_frame(db)
    matches = strength.load_matches(db)
    if validated:
        from fpl_agent.db.eligibility import validated_inputs

        frame, pm, cuts, _ = validated_inputs(db, season, gws, frame, pm)
        cutoff_fn = cuts.get
    sim = SimConfig(n=cfg.samples)

    squad: list[SquadPlayer] = list(start.squad) if start else []
    bank, ft = (start.bank, start.free_transfers) if start else (BUDGET, 1)
    chips_used: list[tuple[str, int]] = list(start.chips_used) if start else []
    model, fitted_at = None, None
    out: list[ReplayWeek] = []
    for gw in gws:
        hz = [g for g in range(gw, min(gw + cfg.horizon, 39))
              if not project.fixtures_for(db, season, [g]).empty]  # fmt: skip
        if not hz or hz[0] != gw:
            continue
        fx = project.fixtures_for(db, season, [gw])
        cutoff = cutoff_fn(gw) if cutoff_fn else project.approx_deadline(fx["kickoff"].min())
        as_of = cutoff.date()
        if model is None or fitted_at is None or gw - fitted_at >= cfg.refit_every:
            # fitted on what the cutoff could see; reused weeks have later cutoffs
            model = minutes.MinutesModel.fit(project.history_as_of(frame, db, season, gw, cutoff))
            fitted_at = gw
        if pool_fn:
            pool = pool_fn(db, season, gw)
        elif validated:  # the observed pre-deadline snapshot, availability as FPL showed it
            pool = pool_as_of(db, season, gw, cutoff)
        else:  # exploratory: archive rows are kickoff-stamped, so not cut by observation time
            pool = pool_as_of(db, season, gw).assign(status="a", chance=None)
        proj = project.build_projection(
            db, season, hz, as_of=as_of, cfg=sim, rules=rules, minutes_model=model,
            ratings=strength.fit_ratings(matches, as_of), pool=pool, frame=frame, pm=pm,
            cutoff=cutoff, known_at_odds=validated,
        )  # fmt: skip
        price = dict(zip(pool["player_id"].astype(int), pool["price"].astype(int), strict=True))
        team = dict(zip(pool["player_id"].astype(int), pool["team_id"].astype(int), strict=True))
        pos = dict(zip(pool["player_id"].astype(int), pool["position"], strict=True))
        ins: list[int] = []
        outs: list[int] = []
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
            ins, outs = list(d.transfers_in), list(d.transfers_out)
            if wk.chip:
                chips_used.append((wk.chip, gw))
            if wk.chip != "freehit":  # a Free Hit squad is gone the week after
                keep = {p.player_id: p for p in squad}
                squad = [keep.get(p) or SquadPlayer(p, pos[p], team[p], price[p], price[p])
                         for p in wk.squad]  # fmt: skip
                bank = d.bank_after
            if wk.chip not in ("wildcard", "freehit"):  # a chip week keeps the bank as it was
                ft = min(cfg.planner.max_free, ft - min(n_in, ft) + 1)
        pts, mins = actual_week(db, season, gw)
        week_pos = {**{p.player_id: p.position for p in squad}, **pos}
        got, cap_pts, bench = score_week(wk, pts, mins, week_pos, rules)
        res = ReplayWeek(gw, got - HIT * wk.hits, wk.hits, wk.chip, n_in, wk.captain, cap_pts,
                         bench, round(wk.xp, 1),
                         {"plan": wk, "ins": ins, "outs": outs,
                          "xp": dict(zip(proj.meta["player_id"].astype(int),
                                         proj.mean()[:, 0], strict=True))})  # fmt: skip
        out.append(res)
        if progress:
            progress(res)
    return ReplayResult(season, out)
