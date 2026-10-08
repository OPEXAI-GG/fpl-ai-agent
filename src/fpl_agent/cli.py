"""Command line entry point: `fpl-agent snapshot`."""

from __future__ import annotations

import argparse
import sys

from fpl_agent.config import Settings
from fpl_agent.data.calendar import next_event
from fpl_agent.data.fpl_client import FPLClient
from fpl_agent.data.players import fixtures_frame, players_frame
from fpl_agent.data.schemas import Bootstrap
from fpl_agent.data.store import SnapshotStore


def snapshot(settings: Settings) -> int:
    client = FPLClient()
    raw = client.bootstrap_raw()
    fixtures = client.fixtures_raw()
    bs = Bootstrap.model_validate(raw)
    nxt = next_event(bs)
    gw = nxt.id if nxt else None
    store = SnapshotStore(settings.snapshot_dir)
    store.write("bootstrap", gw, raw)
    store.write("fixtures", gw, fixtures)
    store.write_frame("players", players_frame(raw), gw)
    store.write_frame("fixtures", fixtures_frame(fixtures), gw)
    print(f"snapshot saved for GW{gw}: {len(bs.elements)} players, {len(fixtures)} fixtures")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="fpl-agent")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("snapshot", help="pull public FPL data and store a point-in-time snapshot")
    args = parser.parse_args(argv)
    settings = Settings.from_env()
    if args.cmd == "snapshot":
        return snapshot(settings)
    return 2


if __name__ == "__main__":
    sys.exit(main())
