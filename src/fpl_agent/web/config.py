"""Website settings, from the environment (see `web/.env.example` and `docs/website.md`)."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def _int(name: str, default: int) -> int:
    v = os.environ.get(name, "").strip()
    return int(v) if v.isdigit() else default


@dataclass(frozen=True)
class WebSettings:
    data_dir: Path = Path("data_cache/web")  # SQLite state, revisions, forecasts, sample data
    source_db: Path = Path("data_cache/football.duckdb")  # the engine's working database
    season: int = 2026
    samples: int = 1000
    horizon: int = 8
    workers: int = 1  # worker processes started by the demo command
    max_queue: int = 20  # queued plan jobs before new ones are refused
    plans_per_session_hour: int = 12
    lookups_per_session_minute: int = 20
    plan_budget_s: float = 900.0
    allow_admin: bool = False  # /api/admin/* (refresh, forecast) only when explicitly enabled

    @property
    def store_path(self) -> Path:
        return self.data_dir / "app.sqlite"

    @property
    def forecast_dir(self) -> Path:
        return self.data_dir / "forecasts"

    @property
    def sample_dir(self) -> Path:
        return self.data_dir / "sample"

    @property
    def eval_dir(self) -> Path:
        return self.data_dir / "evaluations"

    @classmethod
    def from_env(cls) -> WebSettings:
        d = cls()
        return cls(
            data_dir=Path(os.environ.get("WEB_DATA_DIR", str(d.data_dir))),
            source_db=Path(os.environ.get("WEB_SOURCE_DB", str(d.source_db))),
            season=_int("WEB_SEASON", d.season),
            samples=_int("WEB_SAMPLES", d.samples),
            horizon=_int("WEB_HORIZON", d.horizon),
            workers=max(1, min(_int("WEB_WORKERS", d.workers), 4)),
            max_queue=_int("WEB_MAX_QUEUE", d.max_queue),
            plans_per_session_hour=_int("WEB_PLANS_PER_HOUR", d.plans_per_session_hour),
            lookups_per_session_minute=_int("WEB_LOOKUPS_PER_MINUTE", d.lookups_per_session_minute),
            plan_budget_s=float(_int("WEB_PLAN_BUDGET_S", int(d.plan_budget_s))),
            allow_admin=os.environ.get("WEB_ALLOW_ADMIN", "").strip().lower() == "true",
        )


__all__ = ["WebSettings"]
