"""Runtime settings. Secrets come from the environment only, never from the repo."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

FPL_BASE_URL = "https://fantasy.premierleague.com/api"


@dataclass(frozen=True)
class Settings:
    mode: str = "dry"  # "dry" or "live"; live is never the default
    enabled: bool = True  # kill switch
    entry_id: int | None = None
    snapshot_dir: Path = Path("data_cache/snapshots")

    @property
    def is_live(self) -> bool:
        return self.mode == "live" and self.enabled

    @classmethod
    def from_env(cls) -> Settings:
        mode = os.environ.get("AGENT_MODE", "dry").strip().lower()
        if mode not in ("dry", "live"):
            mode = "dry"
        entry = os.environ.get("FPL_ENTRY_ID")
        return cls(
            mode=mode,
            enabled=os.environ.get("AGENT_ENABLED", "true").strip().lower() != "false",
            entry_id=int(entry) if entry and entry.isdigit() else None,
            snapshot_dir=Path(os.environ.get("SNAPSHOT_DIR", "data_cache/snapshots")),
        )
