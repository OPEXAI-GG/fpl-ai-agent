"""Turn a projection and a squad state into planner inputs."""

from __future__ import annotations

from dataclasses import replace

import pandas as pd

from fpl_agent.data.entry import SquadState
from fpl_agent.model.project import Projection
from fpl_agent.solver.planner import Candidate, SquadPlayer


def to_canonical(state: SquadState, pool: pd.DataFrame) -> SquadState:
    """FPL element ids -> canonical player ids (the projection's ids). Unknown elements raise."""
    m = dict(zip(pool["fpl_id"].astype(int), pool["player_id"].astype(int), strict=True))
    missing = [p.player_id for p in state.squad if p.player_id not in m]
    if missing:
        raise ValueError(f"squad players not in the player database: {missing}")
    team = dict(zip(pool["fpl_id"].astype(int), pool["team_id"].astype(int), strict=True))
    squad = [replace(p, player_id=m[p.player_id], team_id=team[p.player_id]) for p in state.squad]
    return replace(state, squad=squad)


def build_candidates(proj: Projection, squad: list[SquadPlayer]) -> list[Candidate]:
    """One candidate per projected player; squad players with no fixture score zero."""
    mean = proj.mean()
    out = [
        Candidate(int(r.player_id), r.position, int(r.team_id), int(r.price), mean[i].tolist())
        for i, r in enumerate(proj.meta.itertuples())
    ]
    have = {c.player_id for c in out}
    zeros = [0.0] * len(proj.gws)
    out += [Candidate(p.player_id, p.position, p.team_id, p.sell_price, list(zeros))
            for p in squad if p.player_id not in have]  # fmt: skip
    return out


def prune(
    cands: list[Candidate], squad: list[SquadPlayer], per_position: int = 40
) -> list[Candidate]:
    """Keep the squad plus the best candidates per position by total expected points and by
    points per £ (so cheap enablers survive). Keeps the MILP small without removing real options."""
    keep = {p.player_id for p in squad}
    for pos in ("GKP", "DEF", "MID", "FWD"):
        pool = [c for c in cands if c.position == pos]
        top = sorted(pool, key=lambda c: -sum(c.xp))[:per_position]
        value = sorted(pool, key=lambda c: -(sum(c.xp) / max(c.price, 1)))[: per_position // 2]
        keep |= {c.player_id for c in top + value}
    return [c for c in cands if c.player_id in keep]
