from fpl_agent.rules import Rules
from fpl_agent.solver.cover import refine_doubtful_lineup
from fpl_agent.solver.planner import WeekPlan
from tests.test_decide import projection, state_for


def example():
    proj, cands = projection(1, horizon=1, gws=(10,), n=100)
    squad = state_for(cands).squad
    by_pos = {
        pos: [p.player_id for p in squad if p.position == pos]
        for pos in ("GKP", "DEF", "MID", "FWD")
    }
    xi = by_pos["GKP"][:1] + by_pos["DEF"][:3] + by_pos["MID"] + by_pos["FWD"][:2]
    bench = [p.player_id for p in squad if p.player_id not in xi]
    doubt, alternative = by_pos["FWD"][2], by_pos["FWD"][1]
    proj.points[:] = 0
    for p in xi:
        proj.points[proj.row(p)] = 10
    proj.points[proj.row(alternative)] = 3.2
    proj.points[proj.row(doubt), :, :75] = 4
    proj.minutes[proj.row(doubt), :, 75:] = 0
    proj.meta.loc[proj.meta.player_id == doubt, ["status", "chance"]] = ["d", 75]
    week = WeekPlan(
        gw=10,
        squad=[p.player_id for p in squad],
        xi=xi,
        bench=bench,
        captain=by_pos["MID"][0],
        vice=by_pos["MID"][1],
        transfers_in=[],
        transfers_out=[],
        hits=0,
        chip=None,
        ft_before=1,
        xp=0,
    )
    return proj, week, doubt, alternative


def test_cover_can_justify_starting_a_doubt_over_higher_individual_mean():
    proj, week, doubt, other = example()
    assert proj.mean()[proj.row(doubt), 0] < proj.mean()[proj.row(other), 0]
    result = refine_doubtful_lineup(week, proj, Rules.load(2026))
    assert doubt in week.xi and other in week.bench
    assert result["after"] > result["before"] + 0.5


def test_cameo_risk_can_make_benching_the_doubt_better():
    proj, week, doubt, other = example()
    proj.minutes[proj.row(doubt), :, 75:] = 1
    # Same 3.0 xP, but no auto-sub for the zero-point cameos.
    result = refine_doubtful_lineup(week, proj, Rules.load(2026))
    assert other in week.xi and doubt in week.bench
    assert result["after"] >= result["before"]
