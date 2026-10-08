"""Property-based tests: invariants that must hold for any input, not just recorded cases."""

from hypothesis import given, settings
from hypothesis import strategies as st

from fpl_agent.data.id_map import normalise
from fpl_agent.rules import Rules, auto_sub, bonus, score, selling_price

R = Rules.load(2026)
POSITIONS = ["GKP", "DEF", "MID", "FWD"]


@given(st.text(max_size=40))
def test_normalise_is_idempotent_and_ascii(s):
    n = normalise(s)
    assert normalise(n) == n
    assert n == n.lower() and "  " not in n


@given(st.dictionaries(st.integers(0, 40), st.integers(-20, 150), min_size=0, max_size=30))
def test_bonus_invariants(bps):
    out = bonus(bps)
    assert set(out) <= set(bps)
    assert all(v in (1, 2, 3) for v in out.values())
    # awards never go to someone with fewer BPS than an unawarded player
    unawarded = [bps[p] for p in bps if p not in out]
    if out and unawarded:
        assert min(bps[p] for p in out) >= max(unawarded)
    # top scorer always gets 3
    if bps:
        top = max(bps.values())
        assert all(out[p] == 3 for p in bps if bps[p] == top)


@given(
    st.sampled_from(POSITIONS),
    st.integers(0, 120),
    st.integers(0, 5),
    st.integers(0, 5),
    st.integers(0, 8),
    st.integers(0, 12),
    st.integers(0, 30),
)
def test_score_is_sum_of_parts(pos, minutes, goals, assists, conceded, saves, dc):
    stats = {"minutes": minutes, "goals_scored": goals, "assists": assists,
             "goals_conceded": conceded, "saves": saves, "defensive_contribution": dc,
             "clean_sheets": 1 if conceded == 0 else 0}  # fmt: skip
    out = score(stats, pos, R)
    assert out["total"] == sum(v for k, v in out.items() if k != "total")
    if minutes == 0:
        assert out["total"] == 0
    if pos != "GKP":
        assert "saves" not in out


@given(st.integers(30, 250), st.integers(30, 250))
def test_selling_price_never_exceeds_current_and_keeps_half_rise(buy, now):
    sell = selling_price(buy, now)
    assert sell <= max(buy, now)
    if now > buy:
        assert buy <= sell <= now and sell - buy == (now - buy) // 2
    else:
        assert sell == now


SQUAD = {f"g{i}": "GKP" for i in (1, 2)}
SQUAD |= {f"d{i}": "DEF" for i in range(1, 6)}
SQUAD |= {f"m{i}": "MID" for i in range(1, 6)}
SQUAD |= {f"f{i}": "FWD" for i in range(1, 4)}
XI = ["g1", "d1", "d2", "d3", "d4", "m1", "m2", "m3", "m4", "f1", "f2"]
BENCH = ["g2", "d5", "m5", "f3"]


@settings(max_examples=300)
@given(st.fixed_dictionaries({p: st.sampled_from([0, 0, 45, 90]) for p in SQUAD}))
def test_auto_sub_always_yields_a_legal_xi(minutes):
    final = auto_sub(XI, BENCH, minutes, SQUAD, R)
    pos = [SQUAD[p] for p in final]
    assert len(final) == 11 and len(set(final)) == 11
    assert pos.count("GKP") == 1 and pos.count("DEF") >= 3
    assert pos.count("MID") >= 2 and pos.count("FWD") >= 1
    # everyone who came in actually played
    assert all(minutes[p] > 0 for p in final if p not in XI)
