"""The multi-week MILP: legal squads, sensible transfer economics, banked-transfer values, hit
buffers, chips, Free Hit, and agreement with brute force on a tiny problem."""

import itertools

import numpy as np
import pytest

from fpl_agent.solver.planner import (
    Candidate,
    PlanInput,
    PlannerConfig,
    SquadPlayer,
    free_hit_value,
    solve_plan,
)

CFG = PlannerConfig(time_limit=30)


def make_pool(seed=0, n_clubs=8, horizon=4):
    """A pool of players per club: 2 GK, 5 DEF, 5 MID, 3 FWD each, prices and xP by quality."""
    rng = np.random.default_rng(seed)
    cands, pid = [], 0
    shape = {"GKP": 2, "DEF": 5, "MID": 5, "FWD": 3}
    base = {"GKP": 3.2, "DEF": 3.6, "MID": 4.2, "FWD": 4.6}
    for club in range(1, n_clubs + 1):
        for pos, n in shape.items():
            for k in range(n):
                pid += 1
                q = rng.uniform(0.2, 1.0) if k < 2 or pos == "FWD" else rng.uniform(0.0, 0.5)
                xp = [max(base[pos] * q * rng.uniform(0.85, 1.15), 0.0) for _ in range(horizon)]
                price = int(40 + q * 60 + (10 if pos in ("MID", "FWD") else 0))
                cands.append(Candidate(pid, pos, club, price, xp))
    return cands


def initial_squad(cands, bank=5):
    """A legal, mediocre squad: the cheapest valid players, no more than 2 per club."""
    need = {"GKP": 2, "DEF": 5, "MID": 5, "FWD": 3}
    chosen, per_club = [], {}
    for pos, n in need.items():
        pool = sorted((c for c in cands if c.position == pos), key=lambda c: c.price)
        for c in pool:
            if sum(1 for x in chosen if x.position == pos) == n:
                break
            if per_club.get(c.team_id, 0) < 2:
                chosen.append(c)
                per_club[c.team_id] = per_club.get(c.team_id, 0) + 1
    squad = [SquadPlayer(c.player_id, c.position, c.team_id, c.price, c.price) for c in chosen]
    return squad, bank


def pinput(seed=0, ft=1, bank=20, chips=None, horizon=4, **kw):
    cands = make_pool(seed, horizon=horizon)
    squad, _ = initial_squad(cands)
    return PlanInput(gws=list(range(10, 10 + horizon)), squad=squad, bank=bank, free_transfers=ft,
                     candidates=cands, chips=chips or {}, **kw)  # fmt: skip


def check_legal(plan, pi):
    pos = {c.player_id: c.position for c in pi.candidates}
    club = {c.player_id: c.team_id for c in pi.candidates}
    for w in plan.weeks:
        assert len(w.squad) == 15 and len(set(w.squad)) == 15
        for p, n in {"GKP": 2, "DEF": 5, "MID": 5, "FWD": 3}.items():
            assert sum(pos[i] == p for i in w.squad) == n
        assert max(np.bincount([club[i] for i in w.squad])) <= 3
        assert len(w.xi) == 11 and set(w.xi) <= set(w.squad)
        xi_pos = [pos[i] for i in w.xi]
        assert xi_pos.count("GKP") == 1 and 3 <= xi_pos.count("DEF") <= 5
        assert 2 <= xi_pos.count("MID") <= 5 and 1 <= xi_pos.count("FWD") <= 3
        assert w.captain in w.xi and w.vice in w.xi and w.vice != w.captain
        assert set(w.bench) | set(w.xi) == set(w.squad) and len(w.bench) == 4
        assert pos[w.bench[-1]] == "GKP"


def spend(plan, pi):
    """Replay the transfers and confirm the budget never goes negative."""
    cands = {c.player_id: c for c in pi.candidates}
    sell = {p.player_id: p.sell_price for p in pi.squad}
    bank = pi.bank
    for w in plan.weeks:
        for i in w.transfers_out:
            bank += sell.get(i, cands[i].price)
        for i in w.transfers_in:
            bank -= cands[i].price
            sell.setdefault(i, cands[i].price)
        assert bank >= 0
    return bank


def test_plan_is_legal_every_week_and_within_budget():
    pi = pinput()
    plan = solve_plan(pi, CFG)
    check_legal(plan, pi)
    assert spend(plan, pi) == plan.bank
    assert all(w.hits <= 1 for w in plan.weeks)


def test_free_transfers_are_used_when_they_pay_and_hits_only_when_they_pay_a_lot():
    pi = pinput(seed=1, ft=1, bank=50)
    plan = solve_plan(pi, CFG)
    first = plan.first
    assert len(first.transfers_in) >= 1  # a weak squad should improve with a free transfer
    n = len(first.transfers_in)
    assert first.hits == max(0, n - first.ft_before)
    # raising the hit price removes hits
    strict = solve_plan(pi, PlannerConfig(time_limit=30, hit_buffer=40))
    assert strict.total_hits() == 0


def test_banking_a_free_transfer_is_valued():
    cands = make_pool(2)
    # a perfectly good squad: nothing is worth a transfer
    best = sorted(cands, key=lambda c: -sum(c.xp))
    squad = []
    need = {"GKP": 2, "DEF": 5, "MID": 5, "FWD": 3}
    clubs = {}
    for c in best:
        if (
            sum(1 for s in squad if s.position == c.position) < need[c.position]
            and clubs.get(c.team_id, 0) < 3
        ):
            squad.append(SquadPlayer(c.player_id, c.position, c.team_id, c.price, c.price))
            clubs[c.team_id] = clubs.get(c.team_id, 0) + 1
    pi = PlanInput(gws=[10, 11, 12], squad=squad, bank=0, free_transfers=1, candidates=cands)
    cfg = PlannerConfig(time_limit=30, transfer_epsilon=0.5)
    plan = solve_plan(pi, cfg)
    assert plan.first.transfers_in == []  # rolled
    assert plan.ft_end >= 3  # and the bank grew by rolling every week


def test_fifth_transfer_is_not_wasted():
    pi = pinput(seed=3, ft=5, bank=40)
    plan = solve_plan(pi, CFG)
    assert len(plan.first.transfers_in) >= 1  # at five banked, a transfer is free


def test_wildcard_week_allows_unlimited_free_changes_and_keeps_the_bank():
    pi = pinput(seed=4, ft=1, bank=60, chips={11: "wildcard"})
    plan = solve_plan(pi, CFG)
    wc = plan.weeks[1]
    assert wc.chip == "wildcard" and wc.hits == 0
    assert len(wc.transfers_in) > 3
    base = solve_plan(pinput(seed=4, ft=1, bank=60), CFG)
    assert plan.objective > base.objective  # the chip is worth something on a weak squad
    assert plan.weeks[2].ft_before >= 2  # banked transfers were not consumed by the wildcard


def test_bench_boost_scores_the_bench_and_changes_the_bench_selection():
    pi = pinput(seed=5, chips={11: "bboost"})
    plan = solve_plan(pi, CFG)
    base = solve_plan(pinput(seed=5), CFG)
    assert plan.objective > base.objective
    check_legal(plan, pi)


def test_triple_captain_triples_the_captain():
    pi = pinput(seed=6, chips={12: "3xc"})
    plan = solve_plan(pi, CFG)
    base = solve_plan(pinput(seed=6), CFG)
    cap = plan.weeks[2].captain
    cx = next(c for c in pi.candidates if c.player_id == cap).xp[2]
    assert plan.objective - base.objective == pytest.approx(cfg_decay(2) * cx, rel=0.35)
    assert plan.weeks[2].xp >= base.weeks[2].xp


def cfg_decay(t):
    return PlannerConfig().decay ** t


def test_free_hit_week_leaves_the_real_squad_untouched():
    pi = pinput(seed=7, chips={11: "freehit"})
    budget = sum(p.sell_price for p in pi.squad) + pi.bank
    value, fh_squad = free_hit_value(pi.candidates, 1, budget, CFG)
    pi.free_hit_values = {11: value}
    plan = solve_plan(pi, CFG)
    w = plan.weeks
    assert w[1].chip == "freehit" and w[1].transfers_in == [] and set(w[1].squad) == set(w[0].squad)
    assert len(fh_squad) == 15 and value > 0
    check_legal(plan, pi)


def test_free_hit_team_respects_the_budget_and_beats_a_weak_squad():
    pi = pinput(seed=8)
    budget = sum(p.sell_price for p in pi.squad) + pi.bank
    value, squad = free_hit_value(pi.candidates, 0, budget, CFG)
    price = {c.player_id: c.price for c in pi.candidates}
    assert sum(price[i] for i in squad) <= budget
    weak = sum(
        next(c for c in pi.candidates if c.player_id == p.player_id).xp[0] for p in pi.squad[:11]
    )
    assert value > weak


def test_forced_roll_costs_value_and_makes_no_transfers():
    pi = pinput(seed=9, ft=2, bank=40)
    free = solve_plan(pi, CFG)
    pi.no_transfer_gws = frozenset({pi.gws[0]})
    rolled = solve_plan(pi, CFG)
    assert rolled.first.transfers_in == [] and rolled.objective <= free.objective + 1e-6


def test_squad_players_must_be_candidates():
    pi = pinput()
    pi.candidates = pi.candidates[:-3]
    pi.squad.append(SquadPlayer(99999, "MID", 1, 50, 50))
    with pytest.raises(ValueError):
        solve_plan(pi, CFG)


def test_matches_brute_force_on_a_tiny_one_week_problem():
    """One gameweek, a legal 15-man squad plus one spare DEF and MID: try every single swap."""
    rng = np.random.default_rng(10)
    cands, pid = [], 0
    for pos, n in (("GKP", 2), ("DEF", 6), ("MID", 6), ("FWD", 3)):
        for _ in range(n):
            pid += 1
            cands.append(Candidate(pid, pos, 1 + pid % 8, int(rng.integers(45, 90)),
                                   [float(rng.uniform(0.5, 7.0))]))  # fmt: skip
    spare = {c.player_id for c in cands if c.position in ("DEF", "MID")}
    spare = {max(i for i in spare if next(c for c in cands if c.player_id == i).position == p)
             for p in ("DEF", "MID")}  # fmt: skip
    chosen = {c.player_id for c in cands} - spare
    squad = [SquadPlayer(c.player_id, c.position, c.team_id, c.price, c.price)
             for c in cands if c.player_id in chosen]  # fmt: skip
    cfg = PlannerConfig(time_limit=30, bench_weights=(0, 0, 0), bench_gk_weight=0,
                        transfer_epsilon=0, bank_value_per_tenth=0, ft_values=(0, 0, 0, 0))  # fmt: skip
    pi = PlanInput(gws=[1], squad=squad, bank=40, free_transfers=1, candidates=cands)
    plan = solve_plan(pi, cfg)
    pos = {c.player_id: c.position for c in cands}
    xp = {c.player_id: c.xp[0] for c in cands}
    price = {c.player_id: c.price for c in cands}

    def value(sq):
        """Best XI + captain bonus for a fixed squad, over every legal formation."""
        best = 0.0
        for g in (i for i in sq if pos[i] == "GKP"):
            others = sorted((i for i in sq if pos[i] != "GKP"), key=lambda i: -xp[i])
            for nd, nm, nf in itertools.product(range(3, 6), range(2, 6), range(1, 4)):
                if nd + nm + nf != 10:
                    continue
                pick = []
                for p, n in (("DEF", nd), ("MID", nm), ("FWD", nf)):
                    pick += [i for i in others if pos[i] == p][:n]
                if len(pick) == 10:
                    best = max(
                        best, xp[g] + sum(xp[i] for i in pick) + max(xp[i] for i in [*pick, g])
                    )
        return best

    best = value(chosen)  # keep the squad
    for out in chosen:  # or make any one same-position swap we can afford
        for inn in set(price) - chosen:
            if pos[inn] == pos[out] and price[inn] - price[out] <= 40:
                best = max(best, value((chosen - {out}) | {inn}))
    got = sum(xp[i] for i in plan.first.xi) + xp[plan.first.captain]
    assert got == pytest.approx(best, abs=1e-6)


def test_rank_risk_pulls_the_plan_towards_what_the_field_owns():
    from dataclasses import replace

    pi = pinput(seed=3)
    for c in pi.candidates:
        c.sd = [3.0] * len(c.xp)
    plain = solve_plan(pi, CFG).first
    squad = {p.player_id for p in pi.squad}
    # the field starts every one of our benched outfield players and captains our 3rd best
    field = {i: 1.0 for i in plain.bench[:3] if i in squad}
    third = sorted(plain.xi, key=lambda i: -next(c.xp[0] for c in pi.candidates
                                                 if c.player_id == i))[2]  # fmt: skip
    field[third] = 2.0
    hug = solve_plan(replace(pi, eo=field), replace(CFG, risk_lambda=1.0)).first
    assert hug.captain == third
    assert set(field) - {third} <= set(hug.xi) | set(hug.transfers_out)
    same = solve_plan(replace(pi, eo=field), CFG).first  # risk_lambda 0: the field is ignored
    assert same.xi == plain.xi and same.captain == plain.captain
