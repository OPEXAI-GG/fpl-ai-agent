"""The source registry: which news feeds and social accounts are read, and how often.

The registry is data (`config/news_sources.yaml`, or the file named by NEWS_SOURCES_FILE).
Credentials never live in it: an adapter that needs one reads it from the environment and is
skipped when it is missing.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

CONFIG = Path(__file__).resolve().parents[3] / "config" / "news_sources.yaml"
KINDS = {"rss", "x"}


@dataclass(frozen=True)
class SourceConfig:
    id: str
    kind: str  # "rss" | "x"
    tier: int = 3
    url: str | None = None
    accounts: tuple[str, ...] = ()
    enabled: bool = True
    min_interval_minutes: int = 60
    max_items: int = 60
    max_pages: int = 1
    timeout_seconds: float = 20.0
    max_bytes: int = 2_000_000
    extra: dict[str, Any] = field(default_factory=dict)


class RegistryError(ValueError):
    """The registry file is not valid; nothing is fetched."""


def parse(data: dict[str, Any]) -> list[SourceConfig]:
    if not isinstance(data, dict):
        raise RegistryError("registry must be a mapping with a `sources` list")
    defaults = data.get("defaults") or {}
    out, seen = [], set()
    for raw in data.get("sources") or []:
        if not isinstance(raw, dict):
            raise RegistryError(f"source entry is not a mapping: {raw!r}")
        merged = {**defaults, **raw}
        sid, kind = str(merged.get("id", "")).strip(), str(merged.get("kind", "")).strip()
        if not sid or sid in seen:
            raise RegistryError(f"source id missing or repeated: {sid!r}")
        if kind not in KINDS:
            raise RegistryError(f"source {sid}: unknown kind {kind!r}")
        if kind == "rss" and not str(merged.get("url", "")).startswith("https://"):
            raise RegistryError(f"source {sid}: an rss source needs an https url")
        tier = int(merged.get("tier", 3))
        if tier not in (1, 2, 3):
            raise RegistryError(f"source {sid}: tier must be 1, 2 or 3")
        known = {f for f in SourceConfig.__dataclass_fields__ if f != "extra"}
        out.append(SourceConfig(
            id=sid, kind=kind, tier=tier, url=merged.get("url"),
            accounts=tuple(str(a).lstrip("@") for a in merged.get("accounts") or []),
            enabled=bool(merged.get("enabled", True)),
            min_interval_minutes=int(merged.get("min_interval_minutes", 60)),
            max_items=max(1, min(int(merged.get("max_items", 60)), 200)),
            max_pages=max(1, min(int(merged.get("max_pages", 1)), 5)),
            timeout_seconds=float(merged.get("timeout_seconds", 20)),
            max_bytes=int(merged.get("max_bytes", 2_000_000)),
            extra={k: v for k, v in merged.items() if k not in known},
        ))  # fmt: skip
        seen.add(sid)
    return out


def load(path: str | Path | None = None) -> list[SourceConfig]:
    p = Path(path or os.environ.get("NEWS_SOURCES_FILE") or CONFIG)
    if not p.exists():
        return []
    try:
        return parse(yaml.safe_load(p.read_text()) or {})
    except yaml.YAMLError as exc:
        raise RegistryError(f"{p}: {exc}") from exc
