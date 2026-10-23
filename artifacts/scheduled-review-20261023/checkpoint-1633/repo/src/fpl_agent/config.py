"""Runtime settings. Secrets come from the environment only, never from the repo."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

FPL_BASE_URL = "https://fantasy.premierleague.com/api"
TRUE, FALSE = ("true", "1", "yes", "on"), ("false", "0", "no", "off")


def kill_switch_enabled(raw: str | None) -> bool:
    """AGENT_ENABLED: unset or true/1/yes/on runs the agent; false/0/no/off stops it. Anything
    else is a typo and also stops it: an unreadable switch never enables writes."""
    if raw is None or not raw.strip():
        return True
    return raw.strip().lower() in TRUE


def _hours(raw: str | None, default: float) -> float:
    try:
        v = float(raw) if raw else default
    except ValueError:
        return default
    return v if 0 < v <= 72 else default


@dataclass(frozen=True)
class Settings:
    mode: str = "dry"  # "dry" or "live"; live is never the default
    enabled: bool = True  # kill switch
    entry_id: int | None = None
    snapshot_dir: Path = Path("data_cache/snapshots")
    max_snapshot_hours: float = 6.0  # a live run refuses to write from older FPL data

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
            enabled=kill_switch_enabled(os.environ.get("AGENT_ENABLED")),
            entry_id=int(entry) if entry and entry.isdigit() else None,
            snapshot_dir=Path(os.environ.get("SNAPSHOT_DIR", "data_cache/snapshots")),
            max_snapshot_hours=_hours(os.environ.get("AGENT_MAX_SNAPSHOT_HOURS"), 6.0),
        )
