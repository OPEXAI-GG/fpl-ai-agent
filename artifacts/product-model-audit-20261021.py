"""Reproducible, offline audit probes. No database or account writes.

These expose existing behavior; successful execution does NOT mean the model is correct.
Run: .venv/Scripts/python.exe artifacts/product-model-audit-20261021.py
"""

import json

import numpy as np
import pandas as pd

from fpl_agent.lessons import Week, _hand_moves
from fpl_agent.model.backtest import beats_baselines, summarize
from fpl_agent.model.simulate import PlayerSpec, SimConfig, _minutes
from fpl_agent.rules import Rules
from fpl_agent.rules.engine import score


def main():
    samples = 100_000
    # Twenty plausible outfield candidates: scaling marginal start probabilities
    # to sum to ten does not enforce ten starters in each simulated match.
    squad = [PlayerSpec(i, "MID", 0.8, 1.0, 0.0) for i in range(20)]
    minutes = _minutes(np.random.default_rng(20261021), squad, samples, SimConfig())
    starters = (minutes >= 60).sum(axis=1)  # all starts are long; no cameos in this probe
    # Extreme missing/absent goalkeeper evidence: the sampler still forces one GK.
    keepers = [PlayerSpec(i, "GKP", 0.0001, 1.0, 0.0) for i in range(2)]
    keeper_minutes = _minutes(
        np.random.default_rng(20261021), keepers, samples, SimConfig()
    )
    week = Week(
        gw=8, names={1: "Outgoing", 2: "Incoming"}, position={1: 3, 2: 3},
        pts={1: 2, 2: 2}, mins={1: 90, 2: 90}, xi=[2], bench=[], captain=2,
        multiplier=2, before={1}, transfers=[{"element_in": 2, "element_out": 1}],
        hits=0,
    )
    lesson = _hand_moves(week)[0]
    # Current overlay scales both start and conditional cameo probabilities.
    # A coherent shared availability gate instead multiplies the whole mixture.
    availability, start, cameo = 0.75, 0.8, 0.3
    current_play = availability * start + (1 - availability * start) * availability * cameo
    gated_play = availability * (start + (1 - start) * cameo)
    comparison = pd.DataFrame({
        "position": ["MID"] * 40, "actual": np.arange(40, dtype=float),
        "sim": np.arange(40) + np.array([2.0] * 20 + [0.0] * 20),
        "last5": np.arange(40) + np.array([1.8] * 20 + [np.nan] * 20),
        "fpl_xp": [np.nan] * 40,
    })
    summary = summarize(comparison)
    print(json.dumps({
        "seed": 20261021, "samples": samples,
        "synthetic_inputs_not_real_team_accuracy": True,
        "outfield_starts": {
            "mean": float(starters.mean()), "maximum": int(starters.max()),
            "share_more_than_ten": float((starters > 10).mean()),
            "share_exactly_ten": float((starters == 10).mean()),
        },
        "near_zero_keeper_marginals": {
            "input_start_probability_each": 0.0001,
            "simulated_play_probability_each": (keeper_minutes > 0).mean(axis=0).tolist(),
        },
        "zero_gain_manual_transfer": {"gain": lesson.points, "learning_rule": lesson.rule},
        "unpaired_baseline_comparison": {
            "summary": summary[summary.position == "ALL"].to_dict("records"),
            "current_verdict": beats_baselines(summary),
            "paired_model_rmse": 2.0, "paired_baseline_rmse": 1.8,
            "meaning": "The model loses on common rows but the current comparator says it wins.",
        },
        "zero_minute_yellow_card": score(
            {"minutes": 0, "yellow_cards": 1}, "MID", Rules.nearest(2026)
        ),
        "availability_mixture_example": {
            "availability": availability, "start_if_available": start,
            "cameo_if_available_and_not_starting": cameo,
            "current_overlay_play_probability": current_play,
            "shared_gate_play_probability": gated_play,
            "interpretation": "Semantic inconsistency if availability represents a shared fitness gate; not an empirical calibration result.",
        },
    }, indent=2))


if __name__ == "__main__":
    main()
