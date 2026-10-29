"""After a gameweek: how your team did, from FPL's public endpoints (your real team, including any
changes you made by hand). Points against the average and the managers around 10,000th, overall
rank movement, the captain against the best captain in your XI, what the week's transfers gained
and the points left on the bench."""

from __future__ import annotations

from typing import Any

from fpl_agent.data.ownership import OVERALL_LEAGUE, PAGE_SIZE, TOP


def _num(n: int | None) -> str:
    return f"{n:,}" if n is not None else "?"


def gw_summary(fpl, entry: int, gw: int | None = None) -> str:
    """One message for the latest finished gameweek (or `gw`)."""
    raw = fpl.bootstrap_raw()
    events = {e["id"]: e for e in raw.get("events", [])}
    if gw is None:
        done = [e["id"] for e in raw.get("events", []) if e.get("finished")]
        if not done:
            return "no finished gameweek yet"
        gw = max(done)
    ev = events.get(gw, {})
    names = {e["id"]: e.get("web_name", str(e["id"])) for e in raw.get("elements", [])}
    hist = {h["event"]: h for h in fpl.entry_history_raw(entry).get("current", [])}
    me = hist.get(gw)
    if not me:
        return f"GW{gw}: no points recorded for your team"
    pts = {e["id"]: e["stats"]["total_points"] for e in fpl.live_raw(gw).get("elements", [])}
    picks = fpl.entry_picks_raw(entry, gw)
    chip = picks.get("active_chip")
    lines = [_points_line(gw, me, ev, chip), _rank_line(me, hist.get(gw - 1))]
    lines += _captain_lines(picks.get("picks", []), pts, names, chip)
    lines += _transfer_lines(fpl.entry_transfers_raw(entry), gw, pts, names, me)
    if me.get("points_on_bench") and chip != "bboost":
        lines.append(f"bench: {me['points_on_bench']} points left on the bench")
    near = _top_line(fpl) if ev.get("is_current") else None  # standings show this week only
    if near:
        lines.append(near)
    return "\n".join(lines)


def _points_line(gw: int, me: dict, ev: dict, chip: str | None) -> str:
    avg, top = ev.get("average_entry_score"), ev.get("highest_score")
    extra = f" (average {avg}, highest {top})" if avg is not None else ""
    played = f", {chip} played" if chip else ""
    return f"GW{gw}: {me['points']} points{extra}{played}"


def _rank_line(me: dict, before: dict | None) -> str:
    now = me.get("overall_rank")
    if not before or not before.get("overall_rank") or not now:
        return f"overall rank {_num(now)}"
    move = before["overall_rank"] - now
    way = "up" if move > 0 else "down" if move < 0 else "no change"
    tail = f" {_num(abs(move))}" if move else ""
    return f"overall rank {_num(now)} ({way}{tail} from {_num(before['overall_rank'])})"


def _captain_lines(picks: list[dict], pts: dict, names: dict, chip: str | None) -> list[str]:
    cap = next((p for p in picks if p.get("is_captain")), None)
    xi = [p["element"] for p in picks if p.get("multiplier", 0) > 0]
    if not cap or not xi:
        return []
    got = pts.get(cap["element"], 0)
    best = max(xi, key=lambda i: pts.get(i, 0))
    times = 3 if chip == "3xc" else 2
    line = f"captain {names.get(cap['element'])}: {got} x{times}"
    if pts.get(best, 0) > got:
        line += f"; best in your XI was {names.get(best)} with {pts.get(best, 0)}"
    return [line]


def _transfer_lines(transfers: list[dict], gw: int, pts: dict, names: dict,
                    me: dict) -> list[str]:  # fmt: skip
    week = [t for t in transfers if t.get("event") == gw]
    if not week:
        return []
    ins = [t["element_in"] for t in week]
    outs = [t["element_out"] for t in week]
    gain = sum(pts.get(i, 0) for i in ins) - sum(pts.get(o, 0) for o in outs)
    hits = me.get("event_transfers_cost", 0)
    moves = ", ".join(
        f"{names.get(o)} ({pts.get(o, 0)}) -> {names.get(i)} ({pts.get(i, 0)})"
        for o, i in zip(outs, ins, strict=True)
    )
    tail = f", {gain - hits:+d} after -{hits} in hits" if hits else ""
    return [f"transfers: {moves}: {gain:+d} points{tail}"]


def _top_line(fpl) -> str | None:
    """Average points this week of the 50 managers ranked just above TOP overall."""
    try:
        data: dict[str, Any] = fpl.league_standings_raw(OVERALL_LEAGUE, TOP // PAGE_SIZE)
        rows = data.get("standings", {}).get("results", [])
    except Exception:  # noqa: BLE001  (the comparison is optional)
        return None
    scores = [r["event_total"] for r in rows if "event_total" in r]
    if not scores:
        return None
    return f"managers around {TOP:,}th overall scored {sum(scores) / len(scores):.0f} on average"
