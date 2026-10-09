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
from fpl_agent.execution.fpl_writer import lineup_payload, preview_matches, transfers_payload
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
    tp = transfers_payload(d, st, 77, ids, price, confirmed=False)
    assert tp["confirmed"] is False and tp["event"] == d.gw and tp["chip"] is None
    assert len(tp["transfers"]) == len(d.transfers_in)
    lp = lineup_payload(d, ids)
    assert [p["position"] for p in lp["picks"]] == list(range(1, 16))
    assert (
        sum(p["is_captain"] for p in lp["picks"]) == 1
        and sum(p["is_vice_captain"] for p in lp["picks"]) == 1
    )


def test_preview_comparison_catches_a_different_plan(base):
    d = base[2]
    ids = fid(base)
    good = {
        "transfers": [{"element_in": ids[i]} for i in d.transfers_in],
        "points_cost": 4 * d.hits,
        "bank": d.bank_after,
    }
    assert preview_matches(good, d, ids) == []
    bad = {**good, "points_cost": 4 * d.hits + 4}
    assert preview_matches(bad, d, ids)


class FakeFPL:
    def __init__(self, base, preview_ok=True):
        self.log, self.d, self.ids, self.ok = [], base[2], fid(base), preview_ok

    def __call__(self, req: httpx.Request):
        body = json.loads(req.content) if req.content else {}
        self.log.append((req.method, req.url.path, body.get("confirmed")))
        assert req.headers["X-API-Authorization"] == "Bearer access"
        if req.url.path.endswith("/transfers/"):
            ins = [{"element_in": self.ids[i]} for i in self.d.transfers_in]
            return httpx.Response(
                200,
                json={
                    "transfers": ins,
                    "bank": self.d.bank_after if self.ok else 0,
                    "points_cost": 4 * self.d.hits,
                },
            )
        if req.method == "POST":
            return httpx.Response(200, json={})
        picks = [{"element": self.ids[p], "is_captain": p == self.d.captain} for p in self.d.xi]
        return httpx.Response(200, json={"picks": picks})


def live_exchange():
    class Ex:
        def exchange(self):
            from fpl_agent.execution.auth import Tokens

            return Tokens("access", "refresh")

    return lambda: Ex()


def test_live_flow_previews_then_confirms_then_sets_lineup_and_verifies(base):
    fake = FakeFPL(base)
    http = httpx.Client(transport=httpx.MockTransport(fake))
    c = ctx_for(base, mode="live")
    price = {i: 50 for i in base[2].transfers_in}
    out = execute(base[2], c, RULES, fid(base), price, 77, exchange=live_exchange(), http=http)
    assert out.mode == "live" and out.ok and out.verified, out
    posts = [(m, p, conf) for m, p, conf in fake.log if m == "POST"]
    assert posts[0][2] is False and posts[1][2] is True  # preview strictly before confirm
    assert posts[2][1].endswith("/my-team/77/")


def test_a_preview_that_disagrees_is_not_confirmed(base):
    fake = FakeFPL(base, preview_ok=False)
    http = httpx.Client(transport=httpx.MockTransport(fake))
    out = execute(base[2], ctx_for(base, mode="live"), RULES, fid(base),
                  {i: 50 for i in base[2].transfers_in}, 77, exchange=live_exchange(), http=http)  # fmt: skip
    assert any("preview differs" in b for b in out.blocked)
    assert not any(conf is True for _, _, conf in fake.log)
    # the lineup needs players the blocked transfers would have brought in, so it is not sent
    assert not any(m == "POST" and p.endswith("/my-team/77/") for m, p, _ in fake.log)
    assert any("lineup not set" in b for b in out.blocked)


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
    def refuse(req: httpx.Request):
        if req.method == "POST" and req.url.path.endswith("/transfers/"):
            return httpx.Response(400, text='{"non_field_errors": ["You cannot wildcard now"]}')
        return httpx.Response(200, json={"picks": []})

    http = httpx.Client(transport=httpx.MockTransport(refuse))
    d = base[2]
    out = execute(d, ctx_for(base, mode="live"), RULES, fid(base),
                  {i: 50 for i in d.transfers_in}, 77, exchange=live_exchange(), http=http)  # fmt: skip
    assert any("HTTP 400 POST /api/transfers/" in e and "cannot wildcard" in e for e in out.errors)
    assert "Bearer" not in " ".join(out.errors)
