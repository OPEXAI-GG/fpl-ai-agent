"""football-data.co.uk CSVs -> matches, team_match (incl. xG where published) and odds.

URL pattern: https://www.football-data.co.uk/mmz4281/<SSYY>/<DIV>.csv (E0 PL, E1 Championship,
E2 League One). The 2026/27 files carry HxG/AxG; older seasons do not.
"""

from __future__ import annotations

import io
from datetime import datetime

import pandas as pd

from fpl_agent.db.database import FootballDB, now

DIVISIONS = {"E0": ("ENG1", "Premier League"), "E1": ("ENG2", "Championship"),
             "E2": ("ENG3", "League One")}  # fmt: skip


def url(div: str, season: int) -> str:
    code = f"{season % 100:02d}{(season + 1) % 100:02d}"
    return f"https://www.football-data.co.uk/mmz4281/{code}/{div}.csv"


def _num(row: pd.Series, col: str):
    v = row.get(col)
    return None if v is None or pd.isna(v) else float(v)


def _int(row: pd.Series, col: str):
    v = _num(row, col)
    return None if v is None else int(v)


def parse_date(s: str):
    for fmt in ("%d/%m/%Y", "%d/%m/%y"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    raise ValueError(f"bad date {s!r}")


def load_csv(db: FootballDB, text: str, div: str, season: int) -> int:
    comp, _ = DIVISIONS[div]
    df = pd.read_csv(io.StringIO(text.lstrip("﻿"))).dropna(subset=["HomeTeam", "AwayTeam"])
    stamp = now()
    team_rows, odds_rows, n = [], [], 0
    for _, r in df.iterrows():
        d = parse_date(str(r["Date"]))
        h = db.team_id("football-data", r["HomeTeam"], r["HomeTeam"])
        a = db.team_id("football-data", r["AwayTeam"], r["AwayTeam"])
        fthg, ftag = _int(r, "FTHG"), _int(r, "FTAG")
        mid = db.match_id(
            "football-data",
            None,
            comp,
            season,
            d,
            h,
            a,
            home_goals=fthg,
            away_goals=ftag,
            finished=fthg is not None,
        )
        hxg, axg = _num(r, "HxG"), _num(r, "AxG")
        for team, is_home, gf, ga, xf, xa, sfx in (
            (h, True, fthg, ftag, hxg, axg, "H"),
            (a, False, ftag, fthg, axg, hxg, "A"),
        ):
            other = "A" if is_home else "H"
            team_rows.append(
                {
                    "match_id": mid,
                    "team_id": team,
                    "source": "football-data",
                    "is_home": is_home,
                    "goals_for": gf,
                    "goals_against": ga,
                    "xg_for": xf,
                    "xg_against": xa,
                    "shots": _int(r, f"{sfx}S"),
                    "shots_on_target": _int(r, f"{sfx}ST"),
                    "corners": _int(r, f"{sfx}C"),
                    "fouls": _int(r, f"{sfx}F"),
                    "yellows": _int(r, f"{sfx}Y"),
                    "reds": _int(r, f"{sfx}R"),
                    "ingested_at": stamp,
                }  # fmt: skip
            )
            del other
        for closing, pre, book in ((False, "Avg", "avg"), (True, "AvgC", "avg"),
                                   (False, "Max", "max"), (True, "MaxC", "max")):  # fmt: skip
            home = _num(r, f"{pre}H")
            if home is None:
                continue
            odds_rows.append(
                {
                    "match_id": mid,
                    "source": "football-data",
                    "bookmaker": book,
                    "closing": closing,
                    "home": home,
                    "draw": _num(r, f"{pre}D"),
                    "away": _num(r, f"{pre}A"),
                    "over25": _num(r, f"{pre}>2.5"),
                    "under25": _num(r, f"{pre}<2.5"),
                    "ah_line": _num(r, "AHCh" if closing else "AHh"),
                    "ah_home": _num(r, f"{pre}AHH"),
                    "ah_away": _num(r, f"{pre}AHA"),
                    "ingested_at": stamp,
                }  # fmt: skip
            )
        n += 1
    db.upsert("team_match", pd.DataFrame(team_rows))
    db.upsert("match_odds", pd.DataFrame(odds_rows))
    db.log_odds(pd.DataFrame(odds_rows))
    db.log("football-data", "matches", n, f"{div} {season}")
    return n
