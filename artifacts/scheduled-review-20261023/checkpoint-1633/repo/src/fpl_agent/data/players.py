"""Flatten bootstrap-static into per-player and per-team tables."""

from __future__ import annotations

from typing import Any

import pandas as pd

POSITIONS = {1: "GKP", 2: "DEF", 3: "MID", 4: "FWD"}


def players_frame(bootstrap_raw: dict[str, Any]) -> pd.DataFrame:
    """One row per player, every field FPL returns kept as a column."""
    df = pd.DataFrame(bootstrap_raw["elements"])
    teams = {t["id"]: t["short_name"] for t in bootstrap_raw["teams"]}
    df["team_short"] = df["team"].map(teams)
    df["position"] = df["element_type"].map(POSITIONS)
    df["price"] = df["now_cost"] / 10
    for col in df.columns:
        if df[col].dtype == object:
            conv = pd.to_numeric(df[col], errors="coerce")
            if conv.notna().sum() == df[col].notna().sum() and df[col].notna().any():
                df[col] = conv
    return df


def teams_frame(bootstrap_raw: dict[str, Any]) -> pd.DataFrame:
    return pd.DataFrame(bootstrap_raw["teams"])


def fixtures_frame(fixtures_raw: list[dict[str, Any]]) -> pd.DataFrame:
    rows = [{k: v for k, v in f.items() if k != "stats"} for f in fixtures_raw]
    return pd.DataFrame(rows)
