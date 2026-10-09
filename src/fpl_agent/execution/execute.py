"""Run a decision: rails -> (dry report | live preview -> confirm -> lineup -> verify).

DRY IS THE DEFAULT and is a hard stop: in dry mode no authenticated request is made at all, no
token is exchanged and nothing is sent to FPL; the result says what *would* have been done.
Live execution needs `AGENT_MODE=live` (and `AGENT_ENABLED` not false) *and* credentials, and has
never been run against a real account by this repository's build.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

import httpx

from fpl_agent.execution import safety
from fpl_agent.execution.auth import AuthError, TokenExchange
from fpl_agent.execution.fpl_writer import (
    FPLWriter,
    lineup_payload,
    preview_matches,
    transfers_payload,
)
from fpl_agent.rules import Rules
from fpl_agent.solver.decide import Decision


@dataclass
class Outcome:
    mode: str  # "noop" | "dry" | "live"
    done: list[str] = field(default_factory=list)  # what was (or would have been) done
    blocked: list[str] = field(default_factory=list)  # what the rails stopped, with reasons
    errors: list[str] = field(default_factory=list)
    verified: bool | None = None

    @property
    def ok(self) -> bool:
        return not self.errors


def transfer_steps(d: Decision) -> list[str]:
    steps = [f"transfer {d.name(o)} -> {d.name(i)}"
             for i, o in zip(d.transfers_in, d.transfers_out, strict=True)]  # fmt: skip
    if d.hits:
        steps.append(f"take a -{4 * d.hits} hit")
    if d.chip in ("wildcard", "freehit"):
        steps.append(f"play {d.chip}")
    return steps


def lineup_steps(d: Decision) -> list[str]:
    steps = [f"play {d.chip}"] if d.chip in ("bboost", "3xc") else []
    return steps + [f"set captain {d.name(d.captain)}, vice {d.name(d.vice)}"]


def _why(exc: httpx.HTTPError) -> str:
    """What FPL answered, for the report: status, call and the start of its message."""
    if isinstance(exc, httpx.HTTPStatusError):
        r = exc.response
        body = " ".join(r.text.split())[:300]
        return f"HTTP {r.status_code} {r.request.method} {r.request.url.path}: {body}"
    return type(exc).__name__


def execute(d: Decision, ctx: safety.Context, rules: Rules, fpl_id: dict[int, int],
            price: dict[int, int], entry: int | None,
            exchange: Callable[[], TokenExchange] | None = None,
            http: httpx.Client | None = None) -> Outcome:  # fmt: skip
    s = ctx.settings
    if not s.enabled:
        return Outcome("noop", blocked=["kill switch: AGENT_ENABLED=false"])
    verdict = safety.check(d, ctx, rules)
    out = Outcome("live" if s.is_live else "dry")
    for v in verdict.violations:
        out.blocked.append(str(v))
    do_transfers = bool(d.transfers_in) or d.chip in ("wildcard", "freehit")
    allow_transfers = do_transfers and not verdict.blocked("transfers")
    allow_lineup = not verdict.blocked("lineup")
    if verdict.blocked("global"):
        return out
    owned = {p.player_id for p in ctx.state.squad}
    needs_transfers = (
        not (set(d.xi) | set(d.bench)) <= owned
    )  # the lineup uses players not owned yet
    no_lineup = "lineup not set: it needs players that the transfers did not bring in"
    if not s.is_live:  # dry: report only, no network, no credentials
        if allow_transfers:
            out.done += [f"would {x}" for x in transfer_steps(d)]
        if allow_lineup and (allow_transfers or not needs_transfers):
            out.done += [f"would {x}" for x in lineup_steps(d)]
        elif allow_lineup:
            out.blocked.append(no_lineup)
        return out
    if entry is None:
        out.errors.append("FPL_ENTRY_ID is not set")
        return out
    try:
        tokens = (exchange or TokenExchange.from_env)().exchange()
    except AuthError as exc:
        out.errors.append(str(exc))
        return out
    w = FPLWriter(tokens.access, entry, client=http)
    transfers_done = False
    try:
        if allow_transfers:
            try:
                prev = w.transfers(transfers_payload(d, ctx.state, entry, fpl_id, price, False))
                diff = preview_matches(prev, d, fpl_id)
                if diff:
                    out.blocked.append("preview differs from plan: " + "; ".join(diff))
                else:
                    w.transfers(transfers_payload(d, ctx.state, entry, fpl_id, price, True))
                    out.done += transfer_steps(d)
                    transfers_done = True
            except httpx.HTTPError as exc:
                out.errors.append(f"transfers failed ({_why(exc)}); trying the lineup")
        if allow_lineup and (transfers_done or not needs_transfers):
            w.set_lineup(lineup_payload(d, fpl_id))
            out.done += lineup_steps(d)
        elif allow_lineup:
            out.blocked.append(no_lineup)
        team = w.my_team()
        got = {p["element"]: p for p in team.get("picks", [])}
        want_cap = fpl_id[d.captain]
        out.verified = bool(got) and got.get(want_cap, {}).get("is_captain", False)
        if not out.verified:
            out.errors.append("read-back does not match the plan")
    except httpx.HTTPError as exc:
        out.errors.append(f"FPL request failed: {_why(exc)}")
    return out
