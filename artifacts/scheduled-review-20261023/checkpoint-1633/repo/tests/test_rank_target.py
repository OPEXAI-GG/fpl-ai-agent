from fpl_agent.solver import rank_target
from fpl_agent.solver.rank_target import Chase, chase


class Standings:
    def __init__(self):
        self.pages = []

    def league_standings_raw(self, league, page):
        self.pages.append((league, page))
        first = (page - 1) * 50 + 1
        return {"standings": {"results": [{"rank": r, "total": 700 - r // 100}
                                          for r in range(first, first + 50)]}}  # fmt: skip


HISTORY = {"current": [{"event": 5, "total_points": 295, "overall_rank": 4_925_389},
                       {"event": 6, "total_points": 336, "overall_rank": 6_158_363}]}  # fmt: skip


def test_the_gap_to_the_target_rank_and_what_it_buys_on_the_armband():
    fpl = Standings()
    c = chase(fpl, HISTORY, 7, target=10_000)
    assert fpl.pages == [(314, 200)]  # rank 10,000 is the last row of page 200
    assert c.target_points == 700 - 10_000 // 100 and c.points == 336 and c.weeks_left == 32
    assert c.behind and c.gap == 264 and round(c.per_week, 2) == 8.25
    assert c.captain_slack == rank_target.MAX_SLACK  # capped
    assert "264 points behind" in c.note() and "6,158,363" in c.note()


def test_slack_scales_with_the_points_needed_and_ahead_means_no_chase():
    near = Chase(target=10_000, rank=20_000, points=460, target_points=473, weeks_left=26)
    assert round(near.captain_slack, 3) == round(0.15 * 13 / 26, 3)
    ahead = Chase(target=10_000, rank=5_000, points=480, target_points=473, weeks_left=26)
    assert not ahead.behind and ahead.captain_slack == 0.0 and "clear" in ahead.note()


def test_no_history_or_no_standings_means_no_target(monkeypatch):
    assert chase(Standings(), {"current": []}, 7) is None

    class Empty:
        def league_standings_raw(self, league, page):
            return {"standings": {"results": []}}

    assert chase(Empty(), HISTORY, 7) is None
    monkeypatch.setenv("AGENT_RANK_TARGET", "100,000")
    assert rank_target.target_rank() == 100_000
    monkeypatch.setenv("AGENT_RANK_TARGET", "nonsense")
    assert rank_target.target_rank() == rank_target.TARGET_RANK
