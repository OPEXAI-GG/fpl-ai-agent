"""Report lines that say what the plan was built from and what it compared."""

from __future__ import annotations

from datetime import datetime

from fpl_agent.team.merge import CurrentTeam, Fact


def _money(v) -> str:
    return "?" if v is None else f"£{int(v) / 10:.1f}m"


def team_lines(team: CurrentTeam, now: datetime | None = None) -> list[str]:
    """Where every important field of the team came from, its status and its age."""
    now = now or team.cutoff
    names = team.names
    n = lambda e: names.get(e, str(e))  # noqa: E731
    f = lambda fact: fact.describe(now)  # noqa: E731
    out = [
        f"current team for GW{team.gameweek} (entry {team.entry}; information cutoff "
        f"{team.cutoff:%Y-%m-%d %H:%M} UTC; ages are relative to it):"
    ]
    out.append(f"  squad: {f(team.squad)}")
    out.append(f"  bank: {_money(team.bank.value)}, {f(team.bank)}")
    ft = team.free_transfers
    out.append(f"  free transfers: {ft.value if ft.value is not None else '?'}, {f(ft)}")
    made = team.transfers_made
    out.append(
        f"  transfers already made this gameweek: "
        f"{made.value if made.value is not None else '?'}, {f(made)}"
    )
    by: dict[str, int] = {}
    for fact in team.selling.values():
        by[fact.status] = by.get(fact.status, 0) + 1
    out.append("  selling prices: " + ", ".join(f"{v} {k}" for k, v in sorted(by.items())))
    diffs = [
        f"{n(e)} {_money(x.value)} (estimate {x.note.split()[-1]})"
        for e, x in sorted(team.selling.items())
        if x.note.startswith("estimate was")
    ]
    if diffs:
        out.append("  screen prices that differ from the estimate: " + ", ".join(diffs))
    lu: Fact = team.lineup
    if lu.value:
        xi, bench, cap, vice = lu.value
        out.append(
            f"  lineup as currently set: {f(lu)}; captain {n(cap) if cap else '?'}, "
            f"vice {n(vice) if vice else '?'}; bench {', '.join(n(b) for b in bench)}"
        )
    else:
        out.append(f"  lineup as currently set: {f(lu)}")
    out.append(
        "  chips: "
        + "; ".join(
            f"{c} {x.value} [{x.status}, {x.source}]" for c, x in sorted(team.chips.items())
        )
    )
    out += [f"  unresolved: {u}" for u in team.unresolved]
    out += [f"  preview: {p}" for p in team.previews]
    out += [f"  not used: {s}" for s in team.stale]
    out += [f"  note: {s}" for s in team.notes]
    return out


def comparison_lines(d, names: dict[int, str]) -> list[str]:
    """The transfer counts the planner compared, scored identically."""
    if not d.comparison:
        return (
            [f"transfer options: not compared ({d.comparison_note})"] if d.comparison_note else []
        )
    n = lambda e: names.get(e, str(e))  # noqa: E731
    roll = d.comparison[0]
    out = [
        "transfer options this week (one forecast, the same sampled worlds and thresholds; "
        "xP = expected points less 4 per hit; objective = the planner's full objective: "
        "discounted, bench-weighted, plus banked-transfer and bank value, less hit buffers "
        "and rank risk):"
    ]
    for r in d.comparison:
        if not r["feasible"]:
            out.append(f"  {r['transfers']} transfer(s): {r['reason']}")
            continue
        moves = ", ".join(f"{n(o)} -> {n(i)}" for i, o in zip(r["in"], r["out"], strict=False))
        head = "0 (roll)" if r["transfers"] == 0 else f"{r['transfers']} ({moves})"
        hit = f", hit -{4 * r['hits']}" if r["hits"] else ""
        delta = (
            ""
            if r["transfers"] == 0
            else f" [{r['week_xp'] - roll['week_xp']:+.1f} this week, "
            f"{r['horizon_xp'] - roll['horizon_xp']:+.1f} over the horizon]"
        )
        solve = "" if r["solve"] == "optimal" else f"; solver: {r['solve']}"
        out.append(
            f"  {head}{hit}: xP {r['week_xp']:.1f} this week, {r['horizon_xp']:.1f} over "
            f"the horizon{delta}; objective {r['objective']:.1f}; {r['reason']}{solve}"
        )
    chosen = len(d.transfers_in)
    out.append(f"  chosen: {'roll' if not chosen else f'{chosen} transfer(s)'}")
    return out


def chip_solver_lines(d) -> list[str]:
    rep = d.chip_report or {}
    out = []
    if rep.get("baseline_solve", "optimal") != "optimal":
        out.append(
            f"chip search baseline: {rep['baseline_solve']} (chip gains are measured against it)"
        )
    open_ = [s for s in rep.get("scenarios", []) if s.get("ok") and s.get("optimal") is False]
    if open_:
        out.append(
            "chip scenarios stopped by the solver time limit (feasible, not proven "
            "optimal): "
            + "; ".join(
                f"{s['chip']} GW{s['gw']} found {s['gain']:+.1f}"
                + (
                    f", could be up to {s['max_gain']:+.1f}"
                    if s.get("max_gain") is not None
                    else ", no bound"
                )
                for s in open_
            )
        )
    if d.solve != "optimal":
        out.append(f"followed plan: {d.solve}")
    return out


def action_key(d) -> tuple:
    return (d.chip, tuple(sorted(d.transfers_out)), tuple(sorted(d.transfers_in)))


def scenario_lines(
    primary_ft: int,
    primary,
    alts: list[tuple[int, object]],
    team: CurrentTeam,
    names: dict[int, str],
) -> tuple[list[str], bool]:
    """The advice under every plausible free-transfer balance. Returns (lines, conditional)."""
    if not alts:
        return [], False
    n = lambda e: names.get(e, str(e))  # noqa: E731

    def act(d) -> str:
        if d.chip:
            return f"play {d.chip}"
        if not d.transfers_in:
            return "roll"
        hit = f" (hit -{4 * d.hits})" if d.hits else ""
        return (
            ", ".join(
                f"{n(o)} -> {n(i)}" for i, o in zip(d.transfers_in, d.transfers_out, strict=False)
            )
            + hit
        )

    every = [(primary_ft, primary), *alts]
    same = len({action_key(d) for _, d in every}) == 1
    status = team.free_transfers.status
    head = (
        f"free transfers are {status}, not confirmed: planned for "
        + ", ".join(str(ft) for ft, _ in every)
        + " remaining"
    )
    lines = [head]
    lines += [f"  with {ft} free: {act(d)}; captain {n(d.captain)}" for ft, d in every]
    if same:
        lines.append(f"  the same action holds for every balance: {act(primary)}")
    else:
        lines.append(
            "  CONDITIONAL: the transfer advice depends on the free-transfer count; "
            "the lineup and captain above stand"
        )
    return lines, not same


__all__ = ["team_lines", "comparison_lines", "chip_solver_lines", "scenario_lines"]
