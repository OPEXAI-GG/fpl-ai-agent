"""A transparent differential watchlist, not an ownership-based points adjustment."""

from __future__ import annotations

import numpy as np
import pandas as pd

from fpl_agent.model.project import Projection
from fpl_agent.solver.inputs import _buyable


def differential_watchlist(
    proj: Projection, *, max_price: int = 65, max_owned: float = 10.0, limit: int = 5
) -> list[dict]:
    """Affordable, low-owned players with minutes, ranked against same-position peers.

    Ownership must be observed, never inferred from absence in a top-manager sample.
    xP already includes minutes/fixture risk. No bonus for simply being unpopular.
    """
    if "owned" not in proj.meta or not proj.gws:
        return []
    h = min(4, len(proj.gws))
    mean = proj.mean()[:, :h].sum(axis=1)
    minutes = proj.minutes[:, :h].mean(axis=2).mean(axis=1)
    rows = []
    for i, r in enumerate(proj.meta.itertuples()):
        owned = r.owned
        if (
            pd.isna(owned)
            or not 0 <= float(owned) < max_owned
            or not 0 < r.price <= max_price
            or not _buyable(r.status, r.chance)
            or minutes[i] < 60
        ):
            continue
        peers = np.array(
            [
                j
                for j, p in enumerate(proj.meta.itertuples())
                if p.position == r.position
                and 0 < p.price <= r.price
                and minutes[j] >= 60
                and _buyable(p.status, p.chance)
            ],
            dtype=int,
        )
        baseline = float(np.median(mean[peers])) if len(peers) else float(mean[i])
        edge = float(mean[i]) - baseline
        if edge <= 0:
            continue
        source = getattr(r, "rate_source", None)
        rows.append(
            {
                "player_id": int(r.player_id),
                "price": int(r.price),
                "owned": float(owned),
                "weeks": h,
                "xpts": float(mean[i]),
                "xpts_per_million": float(mean[i]) / (r.price / 10),
                "peer_edge": edge,
                "expected_minutes": float(minutes[i]),
                "evidence": (
                    "position prior; unvalidated gem" if source in (None, "prior") else str(source)
                ),
            }
        )
    return sorted(rows, key=lambda r: (-r["peer_edge"], -r["xpts_per_million"], r["player_id"]))[
        :limit
    ]
