"""Propose (never apply) identity links between unlinked FPL players and FotMob records.

Name linking (`FootballDB.name_link`) leaves an FPL player without FotMob detail when the two
sources spell or position them differently. Appearance agreement can propose the missing link; it
does not prove it, so a proposal needs all of:

* canonical fixture and team evidence: the FPL row's fixture resolves to the canonical match
  (`quality.FPL_FIXTURE_MATCHES`, season-bound) and the FotMob appearance is for the side of that
  match the FPL player belongs to (FPL squad snapshots taken before the cutoff);
* agreement on a match: minutes within MINUTES_TOLERANCE, the same start, and the same goals,
  cards and saves wherever both sources have them (assists differ by definition, so not used);
* at least MIN_SHARED distinct agreeing matches, covering AGREEMENT of the FPL player's
  appearances *and* of the FotMob record's appearances in those fixtures;
* no contradiction: on no match did the two agree on team and minutes but differ on a counted
  event, and the FotMob record never played for the player's side in a fixture FPL says the
  player sat out;
* uniqueness in both directions: no other FotMob record is a rival for the player (consistent with
  them on at least MIN_SHARED matches and on its own appearances), and the record is a rival for
  no other unlinked player;
* no conflicting identity: the record belongs to nobody else. A record already linked to another
  identity that agrees with the player is reported as a conflict, unless that identity's own FPL
  rows this season agree with it (then it is theirs, and ignored).

Evidence counts at a cutoff only if it happened and was stored before it: matches by kickoff
and `ingested_at`, FotMob rows by `ingested_at`, teams by observed squad snapshots taken before it
(reconstructed archive rows never count). Rows without a stored time never count. `history`
re-runs the rule at every date the evidence changed up to the cutoff and gives each accepted pair
the first date from which it was accepted at every later evaluation (`valid_from`): a past
cutoff may only use a link its own stored evidence supported. Which canonical players hold which
ids (`player_ids`) has no history and is read as it is now: it can only remove candidates.
Read-only.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pandas as pd

from fpl_agent.db.database import FootballDB
from fpl_agent.db.quality import FPL_FIXTURE_MATCHES
from fpl_agent.model.features import OBSERVED_SNAPSHOT

MINUTES_TOLERANCE = 1
MIN_SHARED = 2
AGREEMENT = 0.8
STATS = ("goals", "yellow", "red", "saves")
FPL_STATS = {"goals": "goals_scored", "yellow": "yellow_cards", "red": "red_cards",
             "saves": "saves"}  # fmt: skip
FM_STATS = {"goals": "goals", "yellow": "yellow_cards", "red": "red_cards", "saves": "saves"}


def load(db: FootballDB, season: int, cutoff: datetime,
         untimed: bool = False) -> dict[str, pd.DataFrame]:  # fmt: skip
    """The season's evidence observed before `cutoff`: FPL rows on canonical matches, FotMob
    appearances in those matches, observed squad-snapshot teams, which canonical players hold
    which kinds of id, and any active evidence links. A row counts only once it was stored
    (`ingested_at`); a row without that time never counts (fail closed). A row rewritten later
    (a correction or a backfill) carries its new time, so it is unknown before then.
    Reconstructed snapshots (archive rows stamped at kickoff) are not team evidence.
    `untimed=True` (for `revalidate` only) counts a row without a stored time as known at any
    cutoff: such a row can never support a new link, but it can still contradict one."""
    stored = "coalesce({}, TIMESTAMP '1900-01-01')" if untimed else "{}"
    fstats = ", ".join(f"TRY_CAST(p.stats->>'{v}' AS INTEGER) AS {k}" for k, v in FPL_STATS.items())
    fpl = db.query(
        f"SELECT DISTINCT p.player_id AS p, i.match_id AS m, p.kickoff, "
        f"{stored.format('p.ingested_at')} AS ingested_at, p.minutes, "
        f"TRY_CAST(p.stats->>'starts' AS INTEGER) AS starts, {fstats}, "
        "mt.home_team_id AS home, mt.away_team_id AS away "
        f"FROM fpl_points_gw p JOIN {FPL_FIXTURE_MATCHES} i ON i.season = p.season "
        "AND i.source_id = p.fixture_id::VARCHAR JOIN matches mt ON mt.match_id = i.match_id "
        "WHERE p.season = ? AND p.fixture_id <> 0 AND p.player_id IS NOT NULL "
        f"AND p.kickoff < ? AND {stored.format('p.ingested_at')} < ?",
        [season, cutoff, cutoff])  # fmt: skip
    mstats = ", ".join(f"max(CASE WHEN s.stat = '{v}' THEN s.value END) AS {k}"
                       for k, v in FM_STATS.items())  # fmt: skip
    fm = db.query(
        f"SELECT a.player_id AS q, a.match_id AS m, a.team_id AS team, a.minutes, "
        f"a.started::INTEGER AS started, {mstats}, "
        f"{stored.format('greatest(a.ingested_at, coalesce(max(s.ingested_at), a.ingested_at))')} "
        "AS ingested_at "
        "FROM appearances a "
        "LEFT JOIN player_match_stats s ON s.match_id = a.match_id AND s.player_id = a.player_id "
        "AND s.source = a.source WHERE a.source = 'fotmob' AND a.minutes > 0 "
        f"AND {stored.format('a.ingested_at')} IS NOT NULL "
        "GROUP BY a.player_id, a.match_id, a.team_id, a.minutes, a.started, a.ingested_at",
        [])  # fmt: skip
    fm = fm[fm["m"].isin(set(fpl["m"])) & (fm["ingested_at"] < pd.Timestamp(cutoff))]
    teams = db.query(
        "SELECT DISTINCT player_id AS p, team_id AS team, snapshot_at FROM fpl_players_gw "
        f"WHERE season = ? AND snapshot_at < ? AND {OBSERVED_SNAPSHOT} "
        "AND player_id IS NOT NULL AND team_id IS NOT NULL", [season, cutoff])  # fmt: skip
    kinds = db.query(
        "SELECT player_id, bool_or(source = 'fotmob') AS has_fotmob, "
        "bool_or(source <> 'fotmob') AS has_other, "
        "string_agg(DISTINCT source, ',') FILTER (WHERE source <> 'fotmob') AS others "
        "FROM player_ids GROUP BY player_id")  # fmt: skip
    links = pd.DataFrame(columns=["from_player_id", "to_player_id"])
    if "player_links" not in getattr(db, "missing_tables", []):
        try:
            links = db.query("SELECT from_player_id, to_player_id FROM player_links "
                             "WHERE rolled_back_at IS NULL")  # fmt: skip
        except Exception:  # noqa: BLE001 - an older file has no links
            pass
    return {"fpl": fpl, "fm": fm, "teams": teams, "kinds": kinds, "links": links}


def _eq(a: pd.Series, b: pd.Series) -> pd.Series:
    """Equal, or unknown on either side (an unknown is not a disagreement)."""
    return a.isna() | b.isna() | (a == b)


def propose(ev: dict[str, pd.DataFrame], cutoff: datetime) -> dict[str, Any]:
    """Accepted and rejected proposals at `cutoff`, each with its evidence. `ev` is `load(...)`
    for any cutoff at or after this one; rows at or after `cutoff` are ignored here."""
    at = pd.Timestamp(cutoff)
    fpl = ev["fpl"][(ev["fpl"]["kickoff"] < at) & (ev["fpl"]["ingested_at"] < at)]
    fm = ev["fm"][ev["fm"]["m"].isin(set(fpl["m"])) & (ev["fm"]["ingested_at"] < at)]
    teams = ev["teams"][ev["teams"]["snapshot_at"] < pd.Timestamp(cutoff)]
    kinds = ev["kinds"].set_index("player_id")
    has_fm = set(kinds.index[kinds["has_fotmob"]])
    has_other = set(kinds.index[kinds["has_other"]])
    linked_to = set(ev["links"]["to_player_id"])
    linked_from = set(ev["links"]["from_player_id"])

    # FPL: one row per player-match; differing duplicates make the player's record unusable
    stat_cols = ["minutes", "starts", *STATS]
    dup = fpl.groupby(["p", "m"])[stat_cols].nunique(dropna=False).gt(1).any(axis=1)
    bad_fpl = set(dup[dup].index.get_level_values("p"))
    fpl = fpl.drop_duplicates(["p", "m"])
    dup_fm = fm.duplicated(["q", "m"], keep=False)
    bad_fm = set(fm.loc[dup_fm, "q"])
    fm = fm[~dup_fm]

    # the side of each match a player belongs to, from snapshots before the cutoff
    team_set = teams.groupby("p")["team"].agg(set).to_dict()
    side = []
    for p, h, a in zip(fpl["p"], fpl["home"], fpl["away"], strict=True):
        ts = team_set.get(p, set())
        side.append(h if h in ts and a not in ts else a if a in ts and h not in ts else None)
    fpl = fpl.assign(side=pd.array(side, dtype="Int64"))

    played = fpl[fpl["minutes"] > 0]
    n_p = played.groupby("p")["m"].nunique()
    n_q = fm.groupby("q")["m"].nunique()

    pairs = played.merge(fm, on="m", suffixes=("_f", "_q"))
    pairs = pairs[pairs["side"].notna() & (pairs["side"] == pairs["team"])]
    close = (pairs["minutes_f"] - pairs["minutes_q"]).abs() <= MINUTES_TOLERANCE
    same = _eq(pairs["starts"], pairs["started"])
    for k in STATS:
        same &= _eq(pairs[f"{k}_f"], pairs[f"{k}_q"])
    pairs = pairs.assign(agree=close & same, contra=close & ~same)
    # the record played for the player's side in a fixture FPL says the player sat out
    sat = fpl[(fpl["minutes"] == 0) & fpl["side"].notna()].merge(fm, on="m")
    sat = sat[sat["side"] == sat["team"]].groupby(["p", "q"]).size().rename("absent")

    agg = pairs.groupby(["p", "q"]).agg(hits=("agree", "sum"), contra=("contra", "sum"))
    agg = agg.join(sat, how="left").fillna({"absent": 0}).reset_index()
    agg = agg[agg["hits"] > 0]
    agg["n_p"] = agg["p"].map(n_p)
    agg["n_q"] = agg["q"].map(n_q)
    clean = (agg["contra"] == 0) & (agg["absent"] == 0) & ~agg["q"].isin(bad_fm)
    agg["rival"] = clean & (agg["hits"] >= MIN_SHARED) & (agg["hits"] >= AGREEMENT * agg["n_q"])
    agg["strong"] = agg["rival"] & (agg["hits"] >= AGREEMENT * agg["n_p"])

    unlinked = set(played["p"]) - has_fm - linked_to
    # a record held by another identity is explained when that identity's own FPL rows agree
    explained = set(agg.loc[agg["strong"] & (agg["p"] == agg["q"]), "q"])
    free = (~agg["q"].isin(has_other)) & (~agg["q"].isin(linked_from))
    rival_of_q = agg[agg["rival"] & agg["p"].isin(unlinked) & free].groupby("q")["p"].nunique()

    accepted, rejected = [], []
    for p in sorted(unlinked):
        cand = agg[(agg["p"] == p) & ~agg["q"].isin(explained - {p})]
        cand = cand.sort_values(["hits", "q"], ascending=[False, True])
        rec: dict[str, Any] = {"fpl_player_id": int(p), "appearances": int(n_p.get(p, 0))}
        best = cand.iloc[0] if len(cand) else None
        if best is not None:
            rec["candidate"] = int(best["q"])
            rec.update({k: int(best[k]) for k in ("hits", "contra", "absent")})
            rec["record_appearances"] = int(best["n_q"])
            rec["rivals"] = [int(q) for q in cand.loc[cand["rival"], "q"] if q != best["q"]]
        reason = None
        if p in bad_fpl:
            reason = "contradictory FPL rows"
        elif p not in team_set:
            reason = "no team evidence before the cutoff"
        elif rec["appearances"] < MIN_SHARED:
            reason = "too few appearances"
        elif best is None:
            reason = "no agreeing FotMob record"
        elif int(best["q"]) in bad_fm:
            reason = "duplicate FotMob rows for the candidate"
        elif best["contra"] or best["absent"]:
            reason = "contradicted by the evidence"
        elif not best["strong"]:
            reason = "agreement below threshold"
        elif len(rec["rivals"]):
            reason = "ambiguous: another FotMob record also fits"
        elif best["q"] in has_other:
            others = kinds.at[int(best["q"]), "others"]
            reason = f"conflict: the FotMob record already belongs to {others} identity"
        elif best["q"] in linked_from:
            reason = "conflict: the FotMob record is already linked"
        elif rival_of_q.get(best["q"], 0) > 1:
            reason = "ambiguous: the FotMob record also fits another FPL player"
        if reason:
            rejected.append({**rec, "reason": reason})
        else:
            accepted.append(rec)
    return {"cutoff": cutoff.isoformat(), "accepted": accepted, "rejected": rejected}


def history(db: FootballDB, season: int, cutoff: datetime) -> dict[str, Any]:
    """`propose` at `cutoff`, with each accepted pair's `valid_from`: the earliest evaluation
    date (the day after a match or a squad snapshot) from which the same pair was accepted at
    every later evaluation up to the cutoff. The rule is re-evaluated at every date the season's
    evidence changed, so this is exact for the rule above."""
    ev = load(db, season, cutoff)
    final = propose(ev, cutoff)
    # the evidence changes only when a match is played or a squad snapshot is taken
    stamps = [
        *ev["fpl"]["kickoff"],
        *ev["fpl"]["ingested_at"],
        *ev["fm"]["ingested_at"],
        *ev["teams"]["snapshot_at"],
    ]
    days = sorted({pd.Timestamp(k).normalize() + timedelta(days=1) for k in stamps})
    days = [d.to_pydatetime() for d in days if d < pd.Timestamp(cutoff)] + [cutoff]
    want = {(a["fpl_player_id"], a["candidate"]) for a in final["accepted"]}
    since: dict[tuple[int, int], datetime | None] = dict.fromkeys(want)
    for d in days:
        got = {(a["fpl_player_id"], a["candidate"]) for a in propose(ev, d)["accepted"]}
        for pair in want:
            if pair in got:
                since[pair] = since[pair] or d
            else:
                since[pair] = None
    for a in final["accepted"]:
        vf = since[(a["fpl_player_id"], a["candidate"])]
        a["valid_from"] = (vf or cutoff).isoformat()
    final["season"] = season
    return final


def revalidate(db: FootballDB, season: int, cutoff: datetime | None = None) -> list[dict]:
    """Active links of `season` the rule no longer accepts on the evidence stored before
    `cutoff` (default now), each with the reason. Every active link is set aside and the rule
    re-run as at proposal time, so a link cannot vouch for itself; links written after `cutoff`
    are not checked (they were not usable then). Rows without a stored time count here (as
    contradictions they must not be ignored; this only ever withdraws links). Read-only."""
    at = cutoff or datetime.now(UTC).replace(tzinfo=None)
    ev = load(db, season, at, untimed=True)
    active = db.query(
        "SELECT batch_id, from_player_id, to_player_id FROM player_links WHERE rolled_back_at "
        "IS NULL AND season = ? AND recorded_at <= ?", [season, at])  # fmt: skip
    if active.empty:
        return []
    ev = {**ev, "links": ev["links"].iloc[0:0]}
    r = propose(ev, at)
    accepted = {(a["fpl_player_id"], a["candidate"]) for a in r["accepted"]}
    other = {a["fpl_player_id"]: a["candidate"] for a in r["accepted"]}
    why = {x["fpl_player_id"]: x["reason"] for x in r["rejected"]}
    out = []
    for batch, q, p in active.itertuples(index=False):
        if (p, q) in accepted:
            continue
        if p in other:
            reason = f"the rule now accepts record {other[p]} instead"
        else:
            reason = why.get(p, "no stored evidence for the player by the cutoff")
        out.append({"batch_id": batch, "from_player_id": int(q), "to_player_id": int(p),
                    "reason": reason})  # fmt: skip
    return out


def evidence(db: FootballDB, season: int, cutoff: datetime, p: int, q: int) -> list[dict]:
    """The match-by-match evidence for one pair, for review: every match either played."""
    ev = load(db, season, cutoff)
    f = ev["fpl"][(ev["fpl"]["p"] == p) & (ev["fpl"]["kickoff"] < pd.Timestamp(cutoff))]
    q_rows = ev["fm"][(ev["fm"]["q"] == q) & ev["fm"]["m"].isin(set(f["m"]) | set(ev["fpl"]["m"]))]
    rows = f.merge(q_rows, on="m", how="outer", suffixes=("_fpl", "_fotmob"))
    rows = rows[(rows["minutes_fpl"].fillna(0) > 0) | rows["minutes_fotmob"].notna()]
    keep = [
        "m",
        "kickoff",
        "minutes_fpl",
        "minutes_fotmob",
        "starts",
        "started",
        "team",
        "home",
        "away",
        *[f"{k}_fpl" for k in STATS],
        *[f"{k}_fotmob" for k in STATS],
    ]
    out = rows[[c for c in keep if c in rows.columns]].sort_values("m")
    return [{k: (None if pd.isna(v) else v.isoformat() if hasattr(v, "isoformat") else
                 int(v) if isinstance(v, float) and v.is_integer() else v)
             for k, v in r.items()} for r in out.to_dict("records")]  # fmt: skip
