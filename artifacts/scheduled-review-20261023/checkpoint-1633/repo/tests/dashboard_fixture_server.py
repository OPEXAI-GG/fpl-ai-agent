"""Offline browser fixture server. Never reads or writes the user's database.

Recorded FPL players/fixtures plus explicitly synthetic decisions and projections.
"""

import json
import tempfile
from datetime import timedelta
from pathlib import Path

import pandas as pd

from fpl_agent.dashboard.server import serve
from fpl_agent.db.database import FootballDB, now
from fpl_agent.db.ingest_fpl import load_bootstrap, load_fixtures
from tests.helpers import fixture


def main():
    with tempfile.TemporaryDirectory(prefix="fpl-browser-") as folder:
        root = Path(folder)
        db = FootballDB(root / "fixture.duckdb")
        raw = fixture("bootstrap-static.json")
        season, gw = 2026, 1
        load_bootstrap(db, raw, season, gw, now() - timedelta(hours=7))
        load_fixtures(db, fixture("fixtures.json"), raw, season)
        squad = {}
        for pos, count in (("GKP", 2), ("DEF", 5), ("MID", 6), ("FWD", 2)):
            squad[pos] = [
                r[0]
                for r in db.con.execute(
                    "SELECT player_id FROM fpl_players_gw WHERE position = ? "
                    "ORDER BY player_id LIMIT ?",
                    [pos, count],
                ).fetchall()
            ]
        xi = squad["GKP"][:1] + squad["DEF"][:3] + squad["MID"][:5] + squad["FWD"][:2]
        bench = squad["GKP"][1:] + squad["DEF"][3:] + squad["MID"][5:]
        at = now()
        for week in range(gw, gw + 3):
            db.append(
                "projections",
                pd.DataFrame(
                    [
                        {
                            "run_id": "browser-fixture",
                            "model": "synthetic-test-only",
                            "created_at": at,
                            "season": season,
                            "gw": week,
                            "player_id": pid,
                            "xpts": 3.0 + i / 10,
                            "detail": json.dumps({"q10": 0, "q90": 9, "exp_minutes": 80}),
                        }
                        for i, pid in enumerate(xi + bench)
                    ]
                ),
            )
        db.append(
            "decisions",
            pd.DataFrame(
                [
                    {
                        "season": season,
                        "gw": gw,
                        "created_at": at,
                        "mode": "dry",
                        "payload": json.dumps(
                            {
                                "xi": xi,
                                "bench": bench,
                                "captain": xi[-1],
                                "vice": xi[-2],
                                "xp": 55.5,
                                "xp_no_transfers": 54,
                                "bank_after": 15,
                                "hits": 0,
                                "transfers_in": [],
                                "transfers_out": [],
                                "warnings": [
                                    "TEST FIXTURE: synthetic decision, not an agent recommendation.",
                                    "<script>alert('unsafe')</script>",
                                ],
                            }
                        ),
                    }
                ]
            ),
        )
        db.close()
        serve(root / "fixture.duckdb", 8772, root / "state")


if __name__ == "__main__":
    main()
