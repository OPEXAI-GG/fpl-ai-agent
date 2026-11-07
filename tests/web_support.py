"""A synthetic world served the way the website sees FPL: a football database on disk, a fake
public FPL API with the same response shapes as the recorded ones, and a service over both.

Nothing here touches the network or the owner's team. Entry ids are fictional."""

from __future__ import annotations

import copy
from datetime import UTC, datetime, timedelta
from pathlib import Path

from fpl_agent.db.database import FootballDB
from fpl_agent.errors import SourceUnavailable
from fpl_agent.model import project
from fpl_agent.web.config import WebSettings
from fpl_agent.web.provider import Gateway
from fpl_agent.web.service import Service
from fpl_agent.web.store import Store
from tests.synth import build_world

SEASON, GW = 2024, 10
CODES = {"GKP": 1, "DEF": 2, "MID": 3, "FWD": 4}
ENTRY_A, ENTRY_B = 1_000_001, 1_000_002
# every first-half chip already played: plans skip the (slow) chip search unless a test asks
ALL_CHIPS_USED = [
    {"name": n, "event": g, "time": "2024-09-01T10:00:00Z"}
    for n, g in (("wildcard", 2), ("freehit", 3), ("bboost", 4), ("3xc", 5))
]


def make_world(root: Path) -> tuple[Path, dict, list]:
    """The synthetic football database on disk, with the bootstrap and fixtures FPL would serve
    for it. Returns (db path, bootstrap, fixtures)."""
    path = Path(root) / "football.duckdb"
    db = FootballDB(path)
    build_world(n_teams=10, seasons=(2023, 2024), seed=5, db=db)
    pool = project.current_pool(db, SEASON, GW)
    names = dict(db.con.execute("SELECT player_id, name FROM players").fetchall())
    db.close()
    deadline = datetime.now(UTC) + timedelta(days=3)
    events = [
        {
            "id": g,
            "name": f"Gameweek {g}",
            "deadline_time": (deadline + timedelta(days=7 * (g - GW))).isoformat(),
            "finished": g < GW,
            "is_current": g == GW - 1,
            "is_next": g == GW,
        }
        for g in range(1, 39)
    ]
    teams = sorted({int(t) for t in pool["team_id"]})
    tmap = {t: i + 1 for i, t in enumerate(teams)}
    elements = [
        {
            "id": int(r.fpl_id),
            "web_name": str(names.get(int(r.player_id), f"P{r.fpl_id}")).split()[0]
            + f" {int(r.fpl_id)}",
            "team": tmap[int(r.team_id)],
            "element_type": CODES[r.position],
            "now_cost": int(r.price),
            "cost_change_start": 0,
            "status": "a",
            "chance_of_playing_next_round": None,
            "news": "",
            "total_points": 10,
            "minutes": 500,
            "selected_by_percent": "5.0",
            "ep_next": "3.0",
        }
        for r in pool.itertuples()
    ]
    boot = {
        "events": events,
        "teams": [
            {"id": i, "name": f"Team {i}", "short_name": f"T{i:02d}", "code": 100 + i}
            for i in tmap.values()
        ],
        "elements": elements,
        "element_types": [
            {"id": v, "singular_name_short": k, "squad_select": n}
            for (k, v), n in zip(CODES.items(), (2, 5, 5, 3), strict=True)
        ],
        "total_players": 1000,
    }
    ids = list(tmap.values())
    fixtures = [
        {
            "id": 1000 + g * 10 + k,
            "event": g,
            "team_h": ids[(k + g) % len(ids)],
            "team_a": ids[(k + g + 5) % len(ids)],
            "kickoff_time": (deadline + timedelta(days=7 * (g - GW), hours=4)).isoformat(),
            "finished": False,
            "team_h_score": None,
            "team_a_score": None,
            "team_h_difficulty": 3,
            "team_a_difficulty": 3,
        }
        for g in range(GW, GW + 6)
        for k in range(len(ids) // 2)
    ]
    return path, boot, fixtures


def squad_of(boot: dict, offset: int = 0) -> list[int]:
    """A legal 15 (2/5/5/3, at most three per club) from the bootstrap, cheapest first."""
    need = {1: 2, 2: 5, 3: 5, 4: 3}
    per: dict[int, int] = {}
    out: list[int] = []
    els = sorted(boot["elements"], key=lambda e: (e["now_cost"], e["id"]))
    els = els[offset:] + els[:offset]
    for e in els:
        if need[e["element_type"]] and per.get(e["team"], 0) < 3:
            out.append(e["id"])
            need[e["element_type"]] -= 1
            per[e["team"]] = per.get(e["team"], 0) + 1
    assert len(out) == 15
    return out


def picks_body(boot: dict, squad: list[int], bank: int = 15) -> dict:
    types = {e["id"]: e["element_type"] for e in boot["elements"]}
    gk = [p for p in squad if types[p] == 1]
    rest = [p for p in squad if types[p] != 1]
    xi = [gk[0], *rest[:10]]
    bench = [gk[1], *rest[10:]]
    order = xi + bench
    return {
        "picks": [
            {
                "element": p,
                "position": i + 1,
                "multiplier": 2 if i == 1 else (1 if i < 11 else 0),
                "is_captain": i == 1,
                "is_vice_captain": i == 2,
            }
            for i, p in enumerate(order)
        ],
        "entry_history": {"event": GW - 1, "bank": bank, "value": 1000, "event_transfers": 1},
        "active_chip": None,
    }


class FakeFPL:
    """The public FPL API for a few fictional entries. `down` makes every call fail."""

    def __init__(self, boot: dict, fixtures: list, teams: dict[int, list[int]], chips=None):
        self.boot, self.fx, self.teams = boot, fixtures, teams
        self.chips: dict[int, list] = chips or {}
        self.down = False
        self.calls: list[str] = []

    def _call(self, name: str):
        self.calls.append(name)
        if self.down:
            raise SourceUnavailable("FPL timed out")

    def bootstrap_raw(self):
        self._call("bootstrap")
        return copy.deepcopy(self.boot)

    def fixtures_raw(self):
        self._call("fixtures")
        return copy.deepcopy(self.fx)

    def _known(self, entry: int) -> None:
        if entry not in self.teams:
            raise SourceUnavailable(f"HTTP 404 for entry {entry}")

    def entry_raw(self, entry: int):
        self._call(f"entry:{entry}")
        self._known(entry)
        return {
            "id": entry,
            "name": f"Fiction {entry % 100}",
            "player_first_name": "Test",
            "player_last_name": f"Manager {entry % 100}",
            "player_region_name": "Nowhere",
            "started_event": 1,
            "years_active": 2,
            "summary_overall_points": 500,
            "summary_overall_rank": 250_000,
            "summary_event_points": 50,
            "summary_event_rank": 1_000_000,
            "current_event": GW - 1,
            "last_deadline_bank": 15,
            "last_deadline_value": 1000,
            "leagues": {"classic": [{"id": 314, "rank_count": 1000}]},
        }

    def entry_history_raw(self, entry: int):
        self._call(f"history:{entry}")
        self._known(entry)
        current = [
            {
                "event": g,
                "points": 50,
                "total_points": 50 * g,
                "rank": 100_000,
                "overall_rank": 300_000 - g * 5_000,
                "bank": 15,
                "value": 1000,
                "event_transfers": 1,
                "event_transfers_cost": 0,
                "points_on_bench": 4,
            }
            for g in range(1, GW)
        ]
        return {"current": current, "past": [], "chips": self.chips.get(entry, ALL_CHIPS_USED)}

    def entry_picks_raw(self, entry: int, gw: int):
        self._call(f"picks:{entry}:{gw}")
        self._known(entry)
        return picks_body(self.boot, self.teams[entry])

    def entry_transfers_raw(self, entry: int):
        self._call(f"transfers:{entry}")
        self._known(entry)
        return []

    def league_standings_raw(self, league: int, page: int = 1):
        self._call(f"standings:{page}")
        return {"standings": {"results": [], "has_next": False}}


def make_service(root: Path, db_path: Path, fake: FakeFPL, **kw) -> Service:
    settings = WebSettings(
        data_dir=Path(root) / "web", source_db=db_path, season=SEASON, samples=120, horizon=3, **kw
    )
    store = Store(settings.store_path)
    return Service(settings, store, Gateway(store, client=fake))
