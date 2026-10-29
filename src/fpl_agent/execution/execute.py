"""Run a decision: rails -> (dry report | live preflight -> transfers -> lineup -> verify).

DRY IS THE DEFAULT and is a hard stop: in dry mode no authenticated request is made at all, no
token is exchanged and nothing is sent to FPL; the result says what *would* have been done.
Live execution needs `AGENT_MODE=live` (and `AGENT_ENABLED` not false) *and* credentials, and has
never been run against a real account by this repository's build.

Live writes are treated as binding and are never repeated blindly: transfers are sent in one
call, after checking the plan against the team FPL holds now. If that call fails or its answer is
lost, the team is read again to see what actually happened before anything else is done.
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
    paired_transfers,
    preflight,
    readback_diff,
    team_squad,
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
    verified: bool | None = None  # True only when FPL's team was read back and matched
    unverified: list[str] = field(default_factory=list)  # what FPL's reply did not show

    @property
    def ok(self) -> bool:
        return not self.errors


def transfer_steps(d: Decision, position: dict[int, str] | None = None) -> list[str]:
    steps = [f"transfer {d.name(o)} -> {d.name(i)}"
             for i, o in paired_transfers(d, position)]  # fmt: skip
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
    # an already active Wildcard needs no transfer call when nothing changes
    chip_week = (
        d.chip in ("wildcard", "freehit")
        and not ctx.state.wildcard_active
        and d.chip != ctx.state.active_chip
    )
    do_transfers = bool(d.transfers_in) or chip_week
    allow_transfers = do_transfers and not verdict.blocked("transfers")
    allow_lineup = not verdict.blocked("lineup")
    if verdict.blocked("global"):
        return out
    owned = {p.player_id for p in ctx.state.squad}
    pos = dict(zip(ctx.pool["player_id"].astype(int), ctx.pool["position"], strict=True))
    pos.update({p.player_id: p.position for p in ctx.state.squad})
    needs_transfers = (
        not (set(d.xi) | set(d.bench)) <= owned
    )  # the lineup uses players not owned yet
    no_lineup = "lineup not set: it needs players that the transfers did not bring in"
    if not s.is_live:  # dry: report only, no network, no credentials
        if allow_transfers:
            try:
                out.done += [f"would {x}" for x in transfer_steps(d, pos)]
            except ValueError as exc:
                out.blocked.append(f"transfers not sent: {exc}")
                allow_transfers = False
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
    inv = {f: p for p, f in fpl_id.items()}
    try:
        team = w.my_team()
    except httpx.HTTPError as exc:
        out.errors.append(f"current team could not be read ({_why(exc)}): nothing was sent")
        return out
    held = team_squad(team)
    if len(held) != 15:
        out.errors.append(f"current team has {len(held)} players, not 15: nothing was sent")
        return out
    squad, bank = held, None  # what FPL should hold once this run is done
    transfers_done, chip = False, None
    if allow_transfers:
        want_in = {fpl_id[i] for i in d.transfers_in}
        want_out = {fpl_id[o] for o in d.transfers_out}
        target = (held - want_out) | want_in
        if want_in and want_in <= held and not want_out & held:
            # an earlier run's transfers went through: nothing left to send
            out.done.append("transfers already in place on FPL: nothing to send")
            squad, transfers_done = held, True
        else:
            pf = preflight(team, d, fpl_id, price)
            out.unverified += pf.unverified
            payload = None
            if pf.problems:
                out.blocked.append("transfers not sent: " + "; ".join(pf.problems))
            else:
                try:
                    sell = {inv[f]: v for f, v in pf.sell.items() if f in inv}
                    payload = transfers_payload(d, ctx.state, entry, fpl_id, price, True, pos,
                                                sell)  # fmt: skip
                except ValueError as exc:  # no same-position pairing: send nothing
                    out.blocked.append(f"transfers not sent: {exc}")
            if payload is not None:
                failure = None
                try:
                    w.transfers(payload)  # binding: sent once, never repeated
                except httpx.HTTPError as exc:
                    failure = _why(exc)
                try:  # see what FPL actually did before going further
                    now = team_squad(w.my_team())
                except httpx.HTTPError as exc:
                    out.errors.append(
                        f"transfers {'failed (' + failure + ')' if failure else 'sent'} and the "
                        f"team could not be read back ({_why(exc)}): stopped, not retried"
                    )
                    return out
                if now == target and (want_in or not failure):
                    squad, bank, transfers_done = target, pf.bank_after, True
                    chip = d.chip if d.chip in ("wildcard", "freehit") else None
                    out.done += transfer_steps(d, pos)
                    if failure:
                        out.done.append(f"FPL answered with an error ({failure}) but the "
                                        "transfers are in place")  # fmt: skip
                elif now == held:
                    why = failure or "FPL accepted the call but the team did not change"
                    out.errors.append(f"transfers not applied ({why}); not retried")
                else:
                    out.errors.append(
                        f"after the transfers call the team matches neither the plan nor the "
                        f"previous squad (missing {sorted(target - now)}, unexpected "
                        f"{sorted(now - target)}); nothing else was sent"
                    )
                    return out
    lineup = None
    if allow_lineup and (transfers_done or not needs_transfers):
        lineup = lineup_payload(d, fpl_id, pos)
        try:
            w.set_lineup(lineup)
            out.done += lineup_steps(d)
        except httpx.HTTPError as exc:  # the read-back below shows whether it took effect
            out.errors.append(f"lineup call failed ({_why(exc)})")
        if d.chip in ("bboost", "3xc"):
            chip = d.chip
    elif allow_lineup:
        out.blocked.append(no_lineup)
    try:
        after = w.my_team()
    except httpx.HTTPError as exc:
        out.errors.append(f"read-back failed ({_why(exc)}): the result is unverified")
        out.verified = False
        return out
    diffs, unverified = readback_diff(after, squad, lineup, chip, bank)
    out.unverified += [u for u in unverified if u not in out.unverified]
    if diffs:
        out.errors.append("read-back does not match the plan: " + "; ".join(diffs))
    out.verified = not diffs and not out.errors
    return out
