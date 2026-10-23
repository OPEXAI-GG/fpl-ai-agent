"""Safety rails, payloads, auth rotation and execution. All against mock transports: nothing here
(or anywhere in this repository's tests) contacts a real FPL account."""

import json
from dataclasses import replace

import httpx
import pytest

from fpl_agent.config import Settings
from fpl_agent.execution import safety
from fpl_agent.execution.auth import AuthError, TokenExchange
from fpl_agent.execution.execute import execute
from fpl_agent.execution.fpl_writer import lineup_payload, transfers_payload
from fpl_agent.rules import Rules
from fpl_agent.solver.decide import decide
from fpl_agent.solver.planner import PlannerConfig
from tests.test_decide import projection, state_for

RULES = Rules.load(2026)


@pytest.fixture(scope="module")
def base():
    proj, cands = projection(1)
    st = state_for(cands, bank=60)
    d = decide(st, proj, RULES, cfg=PlannerConfig(time_limit=30), use_chips=False)
    assert d.transfers_in  # the weak squad should want to change
    pool = proj.meta.assign(chance=100.0, status="a")
    xp = dict(zip(proj.meta["player_id"], proj.mean()[:, 0], strict=True))
    return proj, st, d, pool, xp


def ctx_for(base, mode="dry", **kw):
    _, st, d, pool, xp = base
    c = safety.Context(Settings(mode=mode), st, pool, xp, bootstrap_next_gw=d.gw)
    return replace(c, **kw)


def rules_hit(v, rule):
    return any(x.rule == rule for x in v.violations)


def test_a_sane_plan_passes_every_rail(base):
    v = safety.check(base[2], ctx_for(base), RULES)
    assert v.ok, [str(x) for x in v.violations]


def test_kill_switch_blocks_everything(base):
    c = ctx_for(base)
    c = replace(c, settings=Settings(enabled=False))
    assert rules_hit(safety.check(base[2], c, RULES), "kill_switch")
    out = execute(base[2], c, RULES, {}, {}, None)
    assert out.mode == "noop" and not out.done


def test_stale_data_blocks(base):
    v = safety.check(base[2], ctx_for(base, bootstrap_next_gw=base[2].gw + 1), RULES)
    assert rules_hit(v, "stale_data") and v.blocked("transfers") and v.blocked("lineup")


def test_unavailable_or_doubtful_buys_and_starters_are_blocked(base):
    d = base[2]
    pool = base[3].copy()
    pool.loc[pool.player_id == d.transfers_in[0], "chance"] = 50.0
    v = safety.check(d, ctx_for(base, pool=pool), RULES)
    assert rules_hit(v, "availability_buy") and v.blocked("transfers")
    pool2 = base[3].copy()
    pool2.loc[pool2.player_id == d.xi[0], "status"] = "i"
    v2 = safety.check(d, ctx_for(base, pool=pool2), RULES)
    assert (
        rules_hit(v2, "availability_start") and v2.blocked("lineup") and not v2.blocked("transfers")
    )


def test_doubtful_owned_starter_passes_but_doubtful_purchase_does_not(base):
    d = base[2]
    pool = base[3].copy()
    pool.loc[pool.player_id == d.xi[0], ["status", "chance"]] = ["d", 75.0]
    assert not rules_hit(safety.check(d, ctx_for(base, pool=pool), RULES), "availability_start")
    pool.loc[pool.player_id == d.transfers_in[0], ["status", "chance"]] = ["d", 75.0]
    assert rules_hit(safety.check(d, ctx_for(base, pool=pool), RULES), "availability_buy")


def test_hit_caps(base):
    d = replace(base[2], hits=2)
    assert rules_hit(safety.check(d, ctx_for(base), RULES), "hit_cap_gw")
    d1 = replace(base[2], hits=1)
    c = ctx_for(base, recent_hits={d1.gw - 1: 1, d1.gw - 2: 1})
    assert rules_hit(safety.check(d1, c, RULES), "hit_cap_window")


def test_transfer_count_limited_outside_chips(base):
    d = base[2]
    many = replace(d, transfers_in=d.transfers_in * 4, transfers_out=d.transfers_out * 4)
    assert rules_hit(safety.check(many, ctx_for(base), RULES), "transfer_count")


def test_negative_bank_and_missing_projections_block(base):
    assert rules_hit(safety.check(replace(base[2], bank_after=-1), ctx_for(base), RULES), "budget")
    v = safety.check(base[2], ctx_for(base, projected_share=0.9), RULES)
    assert rules_hit(v, "missing_projections")


def test_transfers_already_made_by_a_human_are_not_overwritten(base):
    c = ctx_for(base)
    c.state.already_transferred = 1
    try:
        assert rules_hit(safety.check(base[2], c, RULES), "already_transferred")
    finally:
        c.state.already_transferred = 0


def test_chip_guard(base):
    d = replace(base[2], chip="wildcard", chip_report={"scenarios": []})
    c = ctx_for(base)
    assert rules_hit(safety.check(d, c, RULES), "wildcard_sensitivity")
    tc = replace(base[2], chip="3xc")
    c2 = ctx_for(base, xp={**base[4], base[2].captain: 3.0})
    assert rules_hit(safety.check(tc, c2, RULES), "triple_captain")
    used = ctx_for(base)
    used.state.chips_used.append(("bboost", 5))
    try:
        assert rules_hit(
            safety.check(replace(base[2], chip="bboost"), used, RULES), "chip_unavailable"
        )
    finally:
        used.state.chips_used.pop()


def test_dry_mode_makes_no_requests_and_reports_what_it_would_do(base):
    calls = []

    def boom(*a, **k):
        calls.append(1)
        raise AssertionError("dry mode must not authenticate")

    out = execute(base[2], ctx_for(base, mode="dry"), RULES, {}, {}, 123, exchange=boom)
    assert out.mode == "dry" and not calls
    assert any(x.startswith("would transfer") for x in out.done)
    assert any("captain" in x for x in out.done)


def test_default_settings_are_dry(monkeypatch):
    monkeypatch.delenv("AGENT_MODE", raising=False)
    assert Settings.from_env().is_live is False
    monkeypatch.setenv("AGENT_MODE", "nonsense")
    assert Settings.from_env().mode == "dry"


# ---- payloads and the live path against a fake FPL --------------------------------------------
def fid(base):
    return {
        p: p + 5000
        for p in base[1]
        and [x.player_id for x in base[1].squad] + base[2].transfers_in + base[2].xi + base[2].bench
    }


def test_payloads(base):
    _, st, d, pool, _ = base
    ids = fid(base)
    price = {i: 50 for i in d.transfers_in}
    tp = transfers_payload(d, st, 77, ids, price)
    assert tp["confirmed"] is True and tp["event"] == d.gw and tp["chip"] is None
    assert len(tp["transfers"]) == len(d.transfers_in)
    lp = lineup_payload(d, ids)
    assert [p["position"] for p in lp["picks"]] == list(range(1, 16))
    assert (
        sum(p["is_captain"] for p in lp["picks"]) == 1
        and sum(p["is_vice_captain"] for p in lp["picks"]) == 1
    )


class FakeFPL:
    """A fake FPL that keeps a team. A transfers POST is binding: it swaps the players (as FPL
    has been seen to do even for a "preview"); a lineup POST sets the slots and armbands."""

    def __init__(
        self,
        base,
        *,
        apply=True,
        status=200,
        lose_reply=False,
        refuse_second=False,
        wrong_out=False,
        already=False,
        bank_delta=0,
        drop_pick=False,
        team=None,
    ):
        _, st, d, _, _ = base
        self.d, self.ids, self.log = d, fid(base), []
        self.apply, self.status, self.lose = apply, status, lose_reply
        self.refuse_second, self.wrong_out, self.drop_pick = refuse_second, wrong_out, drop_pick
        sell = {p.player_id: p.sell_price for p in st.squad}
        out_value = sum(sell[o] for o in d.transfers_out)
        self.team = {
            "picks": [{"element": self.ids[p.player_id], "position": n + 1,
                       "selling_price": p.sell_price, "is_captain": False,
                       "is_vice_captain": False} for n, p in enumerate(st.squad)],
            "transfers": {"bank": d.bank_after - out_value + PRICE * len(d.transfers_in)
                          + bank_delta, "limit": len(d.transfers_in) - d.hits, "made": 0},
            "chips": [],
        } | (team or {})  # fmt: skip
        if already:
            self._swap()

    def _swap(self):
        outs = [self.ids[o] for o in self.d.transfers_out]
        if self.wrong_out:  # FPL sold someone else
            outs = [p["element"] for p in self.team["picks"]
                    if p["element"] not in outs][: len(outs)]  # fmt: skip
        ins = iter(self.ids[i] for i in self.d.transfers_in)
        for p in self.team["picks"]:
            if p["element"] in outs:
                p["element"], p["selling_price"] = next(ins), PRICE
        self.team["transfers"]["bank"] = self.d.bank_after
        self.team["transfers"]["made"] = len(self.d.transfers_in)

    def posts(self, path):
        return [b for m, p, b in self.log if m == "POST" and p.endswith(path)]

    def __call__(self, req: httpx.Request):
        body = json.loads(req.content) if req.content else {}
        self.log.append((req.method, req.url.path, body))
        assert req.headers["X-API-Authorization"] == "Bearer access"
        if req.url.path.endswith("/transfers/"):
            if self.refuse_second and len(self.posts("/transfers/")) > 1:
                return httpx.Response(400, json={"detail": "transfers already made"})
            if self.apply:
                self._swap()
            if self.lose:
                raise httpx.ReadTimeout("reply lost", request=req)
            return httpx.Response(self.status, json={})
        if req.method == "POST":  # lineup
            keep = {p["element"]: p for p in self.team["picks"]}
            self.team["picks"] = [keep[p["element"]] | p for p in body["picks"]]
            if body.get("chip"):
                self.team["chips"] = [{"name": body["chip"], "status_for_entry": "active"}]
            return httpx.Response(200, json={})
        team = json.loads(json.dumps(self.team))
        if self.drop_pick:
            team["picks"] = team["picks"][:14]
        return httpx.Response(200, json=team)


PRICE = 50


def run_live(base, fake, d=None, **ctx):
    http = httpx.Client(transport=httpx.MockTransport(fake))
    d = d or base[2]
    return execute(d, ctx_for(base, mode="live", **ctx), RULES, fid(base),
                   {i: PRICE for i in d.transfers_in}, 77, exchange=live_exchange(),
                   http=http)  # fmt: skip


def test_live_flow_sends_one_binding_transfer_call_then_lineup_and_verifies(base):
    fake = FakeFPL(base)
    out = run_live(base, fake)
    assert out.mode == "live" and out.ok and out.verified, out
    assert len(fake.posts("/transfers/")) == 1 and fake.posts("/transfers/")[0]["confirmed"]
    assert len(fake.posts("/my-team/77/")) == 1
    assert out.unverified == []


def test_first_post_applies_and_a_second_would_fail_so_only_one_is_sent(base):
    fake = FakeFPL(base, refuse_second=True)  # the reported production behaviour
    out = run_live(base, fake)
    assert len(fake.posts("/transfers/")) == 1
    assert fake.posts("/my-team/77/") and out.verified, out


@pytest.mark.parametrize("kind", ["lost reply", "4xx after applying"])
def test_an_error_after_fpl_applied_the_transfers_still_sets_the_lineup(base, kind):
    fake = FakeFPL(base, lose_reply=kind == "lost reply",
                   status=400 if kind == "4xx after applying" else 200)  # fmt: skip
    out = run_live(base, fake)
    assert len(fake.posts("/transfers/")) == 1  # never resubmitted
    assert fake.posts("/my-team/77/") and out.verified and out.ok, out
    assert any("transfers are in place" in x for x in out.done)


def test_transfers_that_did_not_apply_are_not_retried_and_not_verified(base):
    fake = FakeFPL(base, apply=False)
    out = run_live(base, fake)
    assert len(fake.posts("/transfers/")) == 1 and not fake.posts("/my-team/77/")
    assert not out.verified and any("not applied" in e for e in out.errors)


def test_wrong_outgoing_player_stops_everything(base):
    fake = FakeFPL(base, wrong_out=True)
    out = run_live(base, fake)
    assert not fake.posts("/my-team/77/") and not out.verified
    assert any("matches neither" in e for e in out.errors)


def test_partial_squad_sends_nothing(base):
    fake = FakeFPL(base, drop_pick=True)
    out = run_live(base, fake)
    assert not fake.posts("/") and not out.verified and out.errors


@pytest.mark.parametrize("team,why", [
    ({"transfers": {"bank": -999, "limit": 5, "made": 0}}, "bank"),
    ({"transfers": {"bank": None, "limit": 0, "made": 3}}, "points cost"),
])  # fmt: skip
def test_wrong_bank_or_cost_blocks_before_anything_is_sent(base, team, why):
    fake = FakeFPL(base, team=team)
    out = run_live(base, fake)
    assert not fake.posts("/transfers/")
    assert any(why in b for b in out.blocked), out


def test_a_bank_that_differs_after_the_transfers_is_not_verified(base):
    fake = FakeFPL(base)
    real = fake._swap

    def swap_and_charge():
        real()
        fake.team["transfers"]["bank"] -= 1

    fake._swap = swap_and_charge
    out = run_live(base, fake)
    assert not out.verified and any("bank" in e for e in out.errors)


def test_a_lineup_fpl_did_not_take_is_not_verified(base):
    fake = FakeFPL(base)
    real = fake.__call__

    class Ignore:
        def __call__(self, req):
            if req.method == "POST" and req.url.path.endswith("/my-team/77/"):
                fake.log.append(("POST", req.url.path, {}))
                return httpx.Response(200, json={})  # accepted, but nothing changed
            return real(req)

    out = run_live(base, Ignore())
    assert not out.verified and any("captain" in e for e in out.errors)


def test_a_repeated_run_does_not_send_the_transfers_again(base):
    fake = FakeFPL(base, already=True)
    out = run_live(base, fake)
    assert out.verified, out
    assert not fake.posts("/transfers/")
    assert any("already in place" in x for x in out.done)


def test_fields_fpl_does_not_report_are_listed_as_unverified(base):
    fake = FakeFPL(base)
    del fake.team["chips"]
    out = run_live(base, fake)
    assert "active chip" in out.unverified


def live_exchange():
    class Ex:
        def exchange(self):
            from fpl_agent.execution.auth import Tokens

            return Tokens("access", "refresh")

    return lambda: Ex()


def test_blocked_transfers_never_send_a_lineup_of_unowned_players(base):
    d = base[2]
    pool = base[3].copy()
    pool.loc[pool.player_id == d.transfers_in[0], "chance"] = 50.0
    c = ctx_for(base, mode="live", pool=pool)
    fake = FakeFPL(base)
    http = httpx.Client(transport=httpx.MockTransport(fake))
    out = execute(d, c, RULES, fid(base), {}, 77, exchange=live_exchange(), http=http)
    assert out.blocked and not any(m == "POST" for m, _, _ in fake.log)
    dry = execute(d, ctx_for(base, pool=pool), RULES, {}, {}, 77)
    assert not dry.done


def test_auth_failure_alerts_and_touches_nothing(base):
    def bad():
        raise AuthError("paste a fresh refresh token")

    out = execute(base[2], ctx_for(base, mode="live"), RULES, fid(base), {}, 77,
                  exchange=lambda: type("E", (), {"exchange": staticmethod(bad)})())  # fmt: skip
    assert out.errors and not out.done


# ---- auth ---------------------------------------------------------------------------------------
def exchange_with(handler, writes):
    return TokenExchange("rt0", "https://auth.example/token", "cid", lambda n, v: writes.append((n, v)),
                         client=httpx.Client(transport=httpx.MockTransport(handler)))  # fmt: skip


def test_rotated_refresh_token_is_written_back_before_use():
    writes = []
    ex = exchange_with(
        lambda r: httpx.Response(200, json={"access_token": "acc-secret", "refresh_token": "rt1"}),
        writes,
    )
    t = ex.exchange()
    assert t.access == "acc-secret" and writes == [("FPL_REFRESH_TOKEN", "rt1")]
    assert "acc-secret" not in repr(t) and "rt1" not in repr(t)


def test_writeback_failure_aborts_loudly():
    def fail(name, value):
        raise AuthError("could not store")

    ex = TokenExchange("rt0", "https://auth.example/token", "cid", fail,
                       client=httpx.Client(transport=httpx.MockTransport(
                           lambda r: httpx.Response(200, json={"access_token": "a", "refresh_token": "rt1"}))))  # fmt: skip
    with pytest.raises(AuthError):
        ex.exchange()


def test_failed_sign_in_gives_repaste_instructions():
    ex = exchange_with(lambda r: httpx.Response(400, json={"error": "invalid_grant"}), [])
    with pytest.raises(AuthError, match="paste a fresh"):
        ex.exchange()


def test_missing_configuration_is_rejected():
    with pytest.raises(AuthError):
        TokenExchange("", "u", "c", lambda *a: None)


def test_failed_sign_in_says_why_without_leaking_the_token():
    def refuse(request):
        body = {"error": "invalid_grant", "error_description": "token expired"}
        return httpx.Response(400, json=body)

    ex = TokenExchange("secret-rt", "https://x/token", "cid", lambda n, v: None,
                       client=httpx.Client(transport=httpx.MockTransport(refuse)))  # fmt: skip
    with pytest.raises(AuthError, match="HTTP 400 invalid_grant token expired") as e:
        ex.exchange()
    assert "secret-rt" not in str(e.value)


def test_a_refused_transfer_reports_what_fpl_said(base):
    fake = FakeFPL(base, apply=False)

    def refuse(req: httpx.Request):
        if req.method == "POST" and req.url.path.endswith("/transfers/"):
            return httpx.Response(400, text='{"non_field_errors": ["You cannot wildcard now"]}')
        return fake(req)

    out = run_live(base, refuse)
    assert any("HTTP 400 POST /api/transfers/" in e and "cannot wildcard" in e for e in out.errors)
    assert "Bearer" not in " ".join(out.errors)


def test_transfers_are_paired_by_position_because_fpl_rejects_other_swaps():
    from types import SimpleNamespace

    from fpl_agent.execution.fpl_writer import paired_transfers

    # the plan lists who comes in and who goes out in unrelated order
    d = SimpleNamespace(transfers_in=[1, 2, 3], transfers_out=[30, 10, 20], name=lambda p: str(p))
    pos = {1: "GKP", 2: "DEF", 3: "FWD", 10: "GKP", 20: "DEF", 30: "FWD"}
    assert paired_transfers(d, pos) == [(1, 10), (2, 20), (3, 30)]
    with pytest.raises(ValueError, match="no player leaving"):
        paired_transfers(d, {**pos, 3: "MID"})


def test_lineup_puts_the_keepers_in_slots_1_and_12_because_fpl_demands_it():
    from types import SimpleNamespace

    xi = [10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20]  # 14 is the starting keeper
    bench = [21, 22, 23, 24]  # 24 is the spare keeper, listed last
    pos = {p: "MID" for p in xi + bench} | {14: "GKP", 24: "GKP"}
    d = SimpleNamespace(xi=xi, bench=bench, captain=10, vice=11, chip=None)
    picks = lineup_payload(d, {p: p + 1000 for p in xi + bench}, pos)["picks"]
    by_slot = {p["position"]: p["element"] - 1000 for p in picks}
    assert by_slot[1] == 14 and by_slot[12] == 24 and sorted(by_slot) == list(range(1, 16))


def test_lineup_orders_the_xi_by_position_line():
    from types import SimpleNamespace

    xi = list(range(10, 21))
    kinds = ["MID", "FWD", "DEF", "GKP", "MID", "DEF", "FWD", "DEF", "MID", "DEF", "MID"]
    bench = [21, 22, 23, 24]
    pos = dict(zip(xi, kinds, strict=True)) | {21: "DEF", 22: "MID", 23: "FWD", 24: "GKP"}
    d = SimpleNamespace(xi=xi, bench=bench, captain=10, vice=11, chip=None)
    picks = lineup_payload(d, {p: p + 1000 for p in xi + bench}, pos)["picks"]
    order = [{"GKP": 0, "DEF": 1, "MID": 2, "FWD": 3}[pos[p["element"] - 1000]] for p in picks[:11]]
    assert order == sorted(order)
    assert picks[11]["element"] - 1000 == 24


def test_an_active_wildcard_passes_the_chip_rails_and_allows_more_changes(base):
    d = replace(base[2], chip="wildcard", hits=0, chip_report={"scenarios": []})
    c = ctx_for(base)
    c.state.chips_used.append(("wildcard", d.gw))
    c.state.wildcard_active = True
    try:
        v = safety.check(d, c, RULES)
        assert not rules_hit(v, "chip_unavailable") and not rules_hit(v, "wildcard_sensitivity")
        assert not rules_hit(v, "already_transferred")
    finally:
        c.state.chips_used.pop()
        c.state.wildcard_active = False


def test_an_active_wildcard_with_no_changes_sends_no_transfer_call(base):
    d = replace(base[2], chip="wildcard", transfers_in=[], transfers_out=[], hits=0)
    c = ctx_for(base, mode="live")
    c.state.wildcard_active = True
    c.state.chips_used.append(("wildcard", d.gw))
    try:
        out = execute(d, c, RULES, {}, {}, None)  # no entry: stops after the rails, no network
        assert not any("transfer" in x for x in out.done)
    finally:
        c.state.wildcard_active = False
        c.state.chips_used.pop()


def test_a_second_chip_is_blocked_while_one_is_active(base):
    c = ctx_for(base)
    c.state.active_chip = "bboost"
    c.state.chips_used.append(("bboost", base[2].gw))
    try:
        v = safety.check(replace(base[2], chip="3xc"), c, RULES)
        assert rules_hit(v, "second_chip") and v.blocked("global")
        v2 = safety.check(replace(base[2], chip="bboost"), c, RULES)
        assert not rules_hit(v2, "second_chip") and not rules_hit(v2, "chip_unavailable")
    finally:
        c.state.active_chip = None
        c.state.chips_used.pop()


def test_a_replan_with_bench_boost_active_keeps_it_on_the_lineup(base):
    own = [p.player_id for p in base[1].squad]  # a re-plan on the squad already held
    d = replace(base[2], chip="bboost", transfers_in=[], transfers_out=[], hits=0,
                xi=own[:11], bench=own[11:], captain=own[0], vice=own[1])  # fmt: skip
    fake = FakeFPL(base, team={"chips": [{"name": "bboost", "status_for_entry": "active"}]})
    c = ctx_for(base, mode="live")
    c.state.active_chip = "bboost"
    c.state.chips_used.append(("bboost", d.gw))
    try:
        http = httpx.Client(transport=httpx.MockTransport(fake))
        out = execute(d, c, RULES, fid(base), {}, 77, exchange=live_exchange(), http=http)
    finally:
        c.state.active_chip = None
        c.state.chips_used.pop()
    assert not fake.posts("/transfers/")
    assert fake.posts("/my-team/77/")[0]["chip"] == "bboost"
    assert out.verified, out
