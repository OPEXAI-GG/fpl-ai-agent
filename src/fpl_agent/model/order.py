"""Canonical row order for model inputs.

SQL gives no row order without ORDER BY, and DuckDB's parallel scans and aggregates return rows
in a different order from one process to the next. The fits are order-sensitive (an optimiser
sums in row order, gradient boosting splits its validation rows by position, the rolling
features read rows in sequence), and the simulation hands out its random draws by player
position. A one-in-a-million change in a fitted goal rate can then change how many uniforms a
Poisson draw consumes, which re-deals every later draw of that fixture. So every fit and the
simulation put their inputs into one canonical order first, whatever order they arrive in.
"""

from __future__ import annotations

from collections.abc import Sequence

import pandas as pd


def canonical(df: pd.DataFrame, keys: Sequence[str]) -> pd.DataFrame:
    """`df` sorted by `keys`, ties broken by every other column, with a fresh index.

    The tie-break makes the order total even when the keys repeat (duplicate or tied source
    rows), so the result depends only on the rows, never on the order they came in.
    """
    if df.empty:
        return df.reset_index(drop=True)
    keys = [k for k in keys if k in df.columns]
    rest = [c for c in df.columns if c not in keys]
    tmp = df.copy()
    by: list[str] = list(keys)
    for i, c in enumerate(rest):
        col = tmp[c]
        if pd.api.types.is_numeric_dtype(col) or pd.api.types.is_datetime64_any_dtype(col):
            by.append(c)
        else:  # strings, JSON, mixed objects: order by their text
            name = f"__order_{i}"
            tmp[name] = col.map(lambda v: "" if v is None or v is pd.NA else str(v))
            by.append(name)
    tmp = tmp.sort_values(by, kind="mergesort", na_position="last")
    return tmp[list(df.columns)].reset_index(drop=True)
