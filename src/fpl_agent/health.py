"""Run each data source under a guard: a failing source is skipped and reported, never fatal
(unless it is critical), and a summary is produced for the alert."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from fpl_agent.errors import SourceError


@dataclass
class SourceResult:
    name: str
    ok: bool
    critical: bool = False
    rows: int | None = None
    error: str | None = None
    started_at: datetime | None = None  # naive UTC, like the database's timestamps
    finished_at: datetime | None = None


@dataclass
class HealthReport:
    results: list[SourceResult] = field(default_factory=list)

    @property
    def failed(self) -> list[SourceResult]:
        return [r for r in self.results if not r.ok]

    @property
    def critical_failed(self) -> bool:
        return any(r.critical for r in self.failed)

    def summary(self) -> str:
        lines = []
        for r in self.results:
            tag = "ok" if r.ok else ("FAILED (critical)" if r.critical else "skipped")
            extra = f" rows={r.rows}" if r.rows is not None else ""
            err = f": {r.error}" if r.error else ""
            lines.append(f"{r.name}: {tag}{extra}{err}")
        return "\n".join(lines)


def guard(
    report: HealthReport, name: str, fn: Callable[[], Any], critical: bool = False
) -> Any | None:
    """Run fn. Source problems and any unexpected exception become a failed result; the caller
    gets None and carries on with the remaining sources."""
    start = _now()
    try:
        out = fn()
    except SourceError as exc:
        report.results.append(SourceResult(name, False, critical, error=str(exc)[:300],
                                           started_at=start, finished_at=_now()))  # fmt: skip
        return None
    except Exception as exc:  # noqa: BLE001 - a bug in one source must not stop the others
        msg = f"unexpected {type(exc).__name__}: {exc}"
        report.results.append(SourceResult(name, False, critical, error=msg[:300],
                                           started_at=start, finished_at=_now()))  # fmt: skip
        return None
    rows = out if isinstance(out, int) else None
    report.results.append(SourceResult(name, True, critical, rows=rows, started_at=start,
                                       finished_at=_now()))  # fmt: skip
    return out


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)
