"""Local, git-ignored storage of a manager's observations (screens read, corrections made).

Layout: `<root>/<entry>/observations/<UTC time>-<kind>-<screen>-<digest>.json`, one validated
observation per file. Saving the same observation twice writes the same file (idempotent).
Images are never stored here: only the fields read from them.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from pydantic import ValidationError

from fpl_agent.team.observation import Observation

DEFAULT_ROOT = Path("data_cache/team")


def _dir(root: Path, entry: int) -> Path:
    return Path(root) / str(int(entry)) / "observations"


def save(root: Path, obs: Observation) -> Path:
    body = json.dumps(obs.model_dump(mode="json"), sort_keys=True, indent=2)
    digest = hashlib.sha256(body.encode()).hexdigest()[:10]
    stamp = obs.observed_at.strftime("%Y%m%dT%H%M%SZ")
    d = _dir(root, obs.entry)
    d.mkdir(parents=True, exist_ok=True)
    path = d / f"{stamp}-{obs.kind}-{obs.screen or 'fields'}-{digest}.json"
    tmp = path.with_suffix(".tmp")
    tmp.write_text(body + "\n")
    tmp.replace(path)  # atomic: a crash never leaves half a file
    return path


def load(root: Path, entry: int) -> tuple[list[Observation], list[str]]:
    """Every stored observation for `entry`, and a note for each file that no longer
    validates (it is skipped, never half-used)."""
    d = _dir(root, entry)
    out, bad = [], []
    if not d.is_dir():
        return out, bad
    for p in sorted(d.glob("*.json")):
        try:
            out.append(Observation.model_validate_json(p.read_text()))
        except (ValidationError, ValueError) as exc:
            bad.append(f"{p.name}: invalid ({type(exc).__name__}), skipped")
    return out, bad


__all__ = ["DEFAULT_ROOT", "save", "load"]
