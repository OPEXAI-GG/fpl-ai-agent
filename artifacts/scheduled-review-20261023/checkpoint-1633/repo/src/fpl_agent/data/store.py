"""Point-in-time snapshots so any decision can be replayed exactly.

Layout: <root>/<source>/gw<NN>/<UTC timestamp>.json
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pandas as pd


class SnapshotStore:
    def __init__(self, root: Path) -> None:
        self.root = Path(root)

    def write(self, source: str, gw: int | None, payload: Any, now: datetime | None = None) -> Path:
        now = now or datetime.now(UTC)
        folder = self.root / source / (f"gw{gw:02d}" if gw is not None else "season")
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / f"{now.strftime('%Y%m%dT%H%M%SZ')}.json"
        path.write_text(json.dumps(payload, separators=(",", ":")), encoding="utf-8")
        return path

    def latest(self, source: str, gw: int | None = None, before: datetime | None = None) -> Any:
        """Newest snapshot, optionally the newest one taken at or before `before`."""
        folder = self.root / source / (f"gw{gw:02d}" if gw is not None else "season")
        files = sorted(folder.glob("*.json")) if folder.exists() else []
        if before is not None:
            cutoff = before.strftime("%Y%m%dT%H%M%SZ")
            files = [f for f in files if f.stem <= cutoff]
        if not files:
            raise FileNotFoundError(f"no snapshot for {source} gw={gw}")
        return json.loads(files[-1].read_text(encoding="utf-8"))

    def write_frame(self, name: str, df: pd.DataFrame, gw: int | None = None) -> Path:
        folder = self.root / "frames"
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / (f"{name}_gw{gw:02d}.parquet" if gw is not None else f"{name}.parquet")
        df.to_parquet(path, index=False)
        return path
