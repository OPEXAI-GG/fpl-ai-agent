"""The top-10k ownership sample: counting, effective ownership and the per-gameweek store."""

import pandas as pd
import pytest

from fpl_agent.data import ownership
from fpl_agent.db.database import FootballDB


class Field:
    """Standings pages of 50 managers; manager e owns players 1-15, captains 1 unless e is odd
    (then 2), and odd managers start player 15 instead of benching him."""

    def __init__(self):
        self.picks_calls = 0

    def league_standings_raw(self, league, page):
        return {"standings": {"results": [{"entry": page * 100 + i} for i in range(50)]}}

    def entry_picks_raw(self, entry, gw):
        self.picks_calls += 1
        odd = entry % 2
        cap = 2 if odd else 1
        picks = []
        for el in range(1, 16):
            bench = el >= 12 and not (odd and el == 15)
            mult = 0 if bench else (2 if el == cap else 1)
            picks.append({"element": el, "multiplier": mult})
        return {"picks": picks}


def test_sample_counts_ownership_and_effective_ownership():
    df = ownership.sample_field(Field(), 5, sample=40, pages=2, seed=1, min_sample=1).set_index(
        "fpl_id"
    )
    n = int(df["sample"].iloc[0])
    assert n == 40 and df.loc[1, "owned"] == 100.0
    odd = df.loc[15, "eo"] / 100  # share of odd managers (who start player 15)
    assert df.loc[1, "eo"] == pytest.approx(100 * (2 - odd))  # captained by the even ones
    assert df.loc[12, "eo"] == 0.0  # always benched


def test_field_ownership_is_taken_once_per_gameweek(tmp_path):
    db = FootballDB(tmp_path / "f.duckdb")
    fpl = Field()
    first = ownership.field_ownership(db, fpl, 2026, 7)
    calls = fpl.picks_calls
    again = ownership.field_ownership(db, fpl, 2026, 7)
    assert calls > 0 and fpl.picks_calls == calls  # read back, not fetched again
    assert len(first) == len(again) == 15


def test_an_unreadable_field_gives_an_empty_frame(tmp_path):
    db = FootballDB(tmp_path / "f.duckdb")
    out = ownership.field_ownership(db, object(), 2026, 7)  # no standings method at all
    assert isinstance(out, pd.DataFrame) and out.empty


def test_the_request_budget_stops_sampling_and_a_tiny_sample_is_not_stored():
    t = iter(range(0, 10_000, 30))  # every call to the clock is 30 seconds later
    df = ownership.sample_field(Field(), 5, sample=200, budget_s=90, clock=lambda: next(t))
    assert df.empty  # stopped after a few managers: below the minimum, not used
    small = ownership.sample_field(Field(), 5, sample=20, min_sample=20)
    assert not small.empty and int(small["sample"].iloc[0]) == 20
    db = FootballDB()

    class Slow(Field):
        def entry_picks_raw(self, entry, gw):
            raise TimeoutError("slow")

    assert ownership.field_ownership(db, Slow(), 2026, 5).empty
    assert db.counts()["field_ownership"] == 0  # nothing partial kept for the week


def test_standings_requests_also_obey_the_sampling_budget():
    calls = []

    class Counted(Field):
        def league_standings_raw(self, league_id, page=1):
            calls.append(page)
            return super().league_standings_raw(league_id, page)

    times = iter([0, 0, 100, 100])
    result = ownership.sample_field(Counted(), 5, budget_s=90, clock=lambda: next(times))
    assert len(calls) == 1 and result.empty
