"""The after-gameweek summary, built from FPL's public endpoints for your real team."""

from fpl_agent.gw_summary import gw_summary


class FakeFPL:
    def __init__(self, current=True, transfers=None, chip=None):
        self.current, self.transfers, self.chip = current, transfers or [], chip

    def bootstrap_raw(self):
        names = {1: "Raya", 2: "Gabriel", 3: "Saka", 4: "Haaland", 5: "Palmer", 6: "Watkins"}
        return {
            "events": [
                {"id": 5, "finished": True, "average_entry_score": 48, "highest_score": 126},
                {
                    "id": 6,
                    "finished": True,
                    "is_current": self.current,
                    "average_entry_score": 51,
                    "highest_score": 110,
                },
                {"id": 7, "finished": False},
            ],  # fmt: skip
            "elements": [{"id": i, "web_name": n} for i, n in names.items()],
        }

    def entry_history_raw(self, entry):
        return {"current": [
            {"event": 5, "points": 36, "overall_rank": 4_925_389},
            {"event": 6, "points": 70, "overall_rank": 3_100_000, "points_on_bench": 6,
             "event_transfers_cost": 4},
        ]}  # fmt: skip

    def live_raw(self, gw):
        pts = {1: 2, 2: 6, 3: 12, 4: 13, 5: 3, 6: 9}
        return {"elements": [{"id": i, "stats": {"total_points": p}} for i, p in pts.items()]}

    def entry_picks_raw(self, entry, gw):
        picks = [{"element": 1, "multiplier": 1}, {"element": 3, "multiplier": 1},
                 {"element": 4, "multiplier": 2, "is_captain": True},
                 {"element": 6, "multiplier": 0}]  # fmt: skip
        return {"picks": picks, "active_chip": self.chip}

    def entry_transfers_raw(self, entry):
        return self.transfers

    def league_standings_raw(self, league, page):
        assert page == 200  # the page holding ranks 9,951 to 10,000
        return {"standings": {"results": [{"event_total": 60}, {"event_total": 64}]}}


def test_summary_of_the_latest_finished_gameweek():
    moves = [{"event": 6, "element_in": 4, "element_out": 5},
             {"event": 6, "element_in": 3, "element_out": 2},
             {"event": 5, "element_in": 6, "element_out": 1}]  # fmt: skip
    text = gw_summary(FakeFPL(transfers=moves), 1)
    assert "GW6: 70 points (average 51, highest 110)" in text
    assert "overall rank 3,100,000 (up 1,825,389 from 4,925,389)" in text
    assert "captain Haaland: 13 x2" in text and "best in your XI" not in text
    assert "Palmer (3) -> Haaland (13), Gabriel (6) -> Saka (12): +16 points, +12 after -4" in text
    assert "bench: 6 points left on the bench" in text
    assert "managers around 10,000th overall scored 62 on average" in text


def test_summary_of_an_earlier_week_and_chips():
    text = gw_summary(FakeFPL(chip="3xc"), 1, gw=5)
    assert text.startswith("GW5: 36 points (average 48, highest 126), 3xc played")
    assert "overall rank 4,925,389" in text and "10,000th" not in text  # standings are this week's
    assert "captain Haaland: 13 x3" in text
    text = gw_summary(FakeFPL(current=False, chip="bboost"), 1)
    assert "bench:" not in text and "10,000th" not in text
    assert gw_summary(FakeFPL(), 1, gw=7) == "GW7: no points recorded for your team"
