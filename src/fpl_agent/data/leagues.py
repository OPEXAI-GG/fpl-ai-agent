"""The competitions the football database covers, with their FotMob ids.

`comp_id` is the database's own competition key. Calendar-year leagues (Brazil, MLS) label a
season by the year it is played in; everything else by its start year (2026 = 2026/27).
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class League:
    comp_id: str
    fotmob_id: int
    name: str
    country: str
    tier: int  # 1 = top flight; European cups are tier 0 (they link the leagues together)
    cup: bool = False  # cups can repeat a pairing in one season (two legs, knockout rounds)
    # split-season leagues play the same home pairing twice (Scotland 38 games among 12 clubs,
    # Belgium's regular season then play-offs), so a pairing is not one match there either
    repeats: bool = False
    calendar_year: bool = False

    def fotmob_season(self, season: int) -> str:
        return str(season) if self.calendar_year else f"{season}/{season + 1}"


LEAGUES: dict[str, League] = {
    lg.comp_id: lg
    for lg in (
        League("ENG1", 47, "Premier League", "England", 1),
        League("ENG2", 48, "Championship", "England", 2),
        League("ENG3", 108, "League One", "England", 3),
        League("ESP1", 87, "La Liga", "Spain", 1),
        League("ITA1", 55, "Serie A", "Italy", 1),
        League("GER1", 54, "Bundesliga", "Germany", 1),
        League("FRA1", 53, "Ligue 1", "France", 1),
        League("NED1", 57, "Eredivisie", "Netherlands", 1),
        League("POR1", 61, "Liga Portugal", "Portugal", 1),
        League("BEL1", 40, "Belgian Pro League", "Belgium", 1, repeats=True),
        League("SCO1", 64, "Scottish Premiership", "Scotland", 1, repeats=True),
        League("BRA1", 268, "Serie A (Brazil)", "Brazil", 1, calendar_year=True),
        League("UCL", 42, "Champions League", "Europe", 0, cup=True),
        League("UEL", 73, "Europa League", "Europe", 0, cup=True),
        League("UECL", 10216, "Conference League", "Europe", 0, cup=True),
    )
}

# Competitions where a home pairing can happen more than once a season (the date is in the key).
REPEATING = frozenset(lg.comp_id for lg in LEAGUES.values() if lg.cup or lg.repeats)

# What the weekly run pulls by default: England's top three tiers, the feeder leagues and Europe.
DEFAULT_COMPETITIONS = tuple(LEAGUES)
# Leagues FotMob is expected to have xG for; the rest are reported if xG is missing.
XG_EXPECTED = {"ENG1", "ENG2", "ESP1", "ITA1", "GER1", "FRA1", "UCL", "UEL", "UECL"}


def get(comp_id: str) -> League:
    try:
        return LEAGUES[comp_id]
    except KeyError as exc:
        raise KeyError(f"unknown competition {comp_id!r}; known: {sorted(LEAGUES)}") from exc
