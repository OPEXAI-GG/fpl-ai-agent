# ruff: noqa: E501
"""DB02 semantic sampling over RETAINED evidence (read-only; seeded sample; writes
semantic_sampling.json). Retained raw evidence is limited to fpl_players_gw.raw,
fpl_points_gw.stats/explain and news_evidence.payload. FotMob/Understat/football-data raw payloads
are not retained, so FotMob-derived rows are traced only through their normalised rows; that is
reported as a gap, never as external truth. Run from the repo root."""

import json
import random
import sys
from pathlib import Path

import duckdb

sys.path.insert(0, "src")
from fpl_agent.data.id_map import normalise  # noqa: E402

OUT = Path(__file__).parent
con = duckdb.connect("data_cache/football.duckdb", read_only=True)
rng = random.Random(20261022)


def rows(sql, params=None):
    cur = con.execute(sql, params or [])
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, r, strict=True)) for r in cur.fetchall()]


def clean(v):
    return v if isinstance(v, int | float | str | bool | type(None)) else str(v)


res = {
    "seed": 20261022,
    "evidence_retained": {
        "fpl": "fpl_players_gw.raw (full element JSON), fpl_points_gw.stats/explain",
        "fotmob": "NOT retained: only normalised rows (appearances, stats, shots, team_match)",
        "understat": "NOT retained",
        "football_data": "NOT retained",
        "news": "news_evidence.payload (metadata and flags, no article text checked here)",
    },
    "external_truth_note": "Nothing here is verified against a match report; agreement between "
    "retained sources is corroboration only.",
}

# ---- per-match minutes of FPL players and FotMob appearances, keyed by canonical match -------
fpl_min = {}
for r in rows(
    "SELECT i.match_id, x.fpl_id, sum(x.minutes) m FROM fpl_points_gw x JOIN match_ids i ON "
    "i.source='fpl' AND i.source_id = x.fixture_id::VARCHAR GROUP BY 1, 2"
):
    fpl_min.setdefault(r["fpl_id"], {})[r["match_id"]] = r["m"]
fpl_team = {
    r["fpl_id"]: r["team_id"]
    for r in rows(
        "SELECT fpl_id, team_id FROM fpl_players_gw QUALIFY snapshot_at = max(snapshot_at) "
        "OVER (PARTITION BY fpl_id)"
    )
}
fpl_name = {
    r["fpl_id"]: r["n"]
    for r in rows(
        "SELECT i.source_id::INTEGER fpl_id, p.name n FROM player_ids i JOIN players p USING "
        "(player_id) WHERE i.source='fpl'"
    )
}
fpl_pid = {
    r["fpl_id"]: r["player_id"]
    for r in rows("SELECT source_id::INTEGER fpl_id, player_id FROM player_ids WHERE source='fpl'")
}
fpl_pos = {
    r["fpl_id"]: r["position"] for r in rows("SELECT DISTINCT fpl_id, position FROM fpl_players_gw")
}
linked_pids = {
    r["player_id"] for r in rows("SELECT player_id FROM player_ids WHERE source='fotmob'")
}

fm = {}  # fotmob-side canonical player_id -> info
for r in rows(
    "SELECT a.player_id, a.match_id, a.team_id, a.minutes, a.position, i.source_name FROM appearances a "
    "JOIN player_ids i ON i.player_id = a.player_id AND i.source='fotmob' JOIN matches m USING "
    "(match_id) WHERE m.competition_id = 'ENG1'"
):
    d = fm.setdefault(
        r["player_id"],
        {"name": r["source_name"], "pos": r["position"], "team": r["team_id"], "min": {}},
    )
    d["min"][r["match_id"]] = r["minutes"]


def compare(fpl_id, fm_pid):
    """Corroboration of one FPL/FotMob pair: club, shared matches, minutes within 5."""
    f = fm[fm_pid]
    shared = [m for m in f["min"] if m in fpl_min.get(fpl_id, {})]
    close = [m for m in shared if abs(f["min"][m] - fpl_min[fpl_id][m]) <= 5]
    return {
        "same_club": fpl_team.get(fpl_id) == f["team"],
        "shared_matches": len(shared),
        "minutes_within_5": len(close),
    }


# ---- (1) linked vs refused players, midfielders stratified -----------------------------------
linked_fpl = [e for e, p in fpl_pid.items() if p in linked_pids and p in fm]
linked_mid = [e for e in linked_fpl if fpl_pos.get(e) == "MID"]
sample_linked = []
for e in rng.sample(linked_mid, min(10, len(linked_mid))):
    sample_linked.append(
        {
            "fpl_id": e,
            "fpl_name": fpl_name[e],
            "fotmob_name": fm[fpl_pid[e]]["name"],
            **compare(e, fpl_pid[e]),
        }
    )
res["linked_midfielder_sample"] = sample_linked
res["linked_population_agreement"] = {
    "pairs_with_shared_matches": sum(
        1 for e in linked_fpl if compare(e, fpl_pid[e])["shared_matches"]
    ),
    "same_club_all": sum(1 for e in linked_fpl if compare(e, fpl_pid[e])["same_club"]),
    "all_shared_minutes_within_5": sum(
        1
        for e in linked_fpl
        if (c := compare(e, fpl_pid[e]))["shared_matches"]
        and c["shared_matches"] == c["minutes_within_5"]
    ),
    "n_linked_fpl_players_with_fotmob_pl_appearance": len(linked_fpl),
}

# refused by position: FPL candidate with the same normalised name
fm_by_name = {}
for r in rows("SELECT player_id, source_name FROM player_ids WHERE source='fotmob'"):
    fm_by_name.setdefault(normalise(r["source_name"]), []).append(r["player_id"])
fpl_by_name = {}
for e, n in fpl_name.items():
    fpl_by_name.setdefault(normalise(n), []).append(e)
refused = []
for r in rows("SELECT key, detail FROM ingest_state WHERE source='link'"):
    name = r["key"].split(":", 1)[1]
    if not r["detail"].startswith("position"):
        continue
    for pid in fm_by_name.get(name, []):
        for e in fpl_by_name.get(name, []):
            if pid in fm:
                refused.append(
                    {
                        "name": name,
                        "fpl_id": e,
                        "fpl_pos": fpl_pos.get(e),
                        "fotmob_pos": fm[pid]["pos"],
                        "reason": r["detail"],
                        **compare(e, pid),
                    }
                )
verdict = lambda c: (  # noqa: E731
    "corroborated"
    if c["same_club"] and c["shared_matches"] and c["shared_matches"] == c["minutes_within_5"]
    else "contradicted"
    if (not c["same_club"]) or c["minutes_within_5"] < c["shared_matches"]
    else "no overlapping match"
)
for r in refused:
    r["verdict"] = verdict(r)
res["refused_position_pairs"] = {
    "pairs_with_pl_fotmob_appearances": len(refused),
    "corroborated_same_club_and_minutes": sum(r["verdict"] == "corroborated" for r in refused),
    "contradicted": sum(r["verdict"] == "contradicted" for r in refused),
    "no_overlapping_match": sum(r["verdict"] == "no overlapping match" for r in refused),
}
mids = [r for r in refused if r["fpl_pos"] == "MID" or r["fotmob_pos"] == "MID"]
res["refused_midfielder_sample"] = rng.sample(mids, min(10, len(mids)))

# single-word / unmatched FotMob players: club + minutes signature against unlinked FPL players
unl_fpl = [e for e, p in fpl_pid.items() if p not in linked_pids and fpl_min.get(e)]
cands_summary = {
    "fotmob_unlinked_pl_players": 0,
    "unique_signature_match": 0,
    "multiple_signature_matches": 0,
    "none": 0,
}
examples = []
for pid, f in fm.items():
    if f["pos"] is None or pid in {fpl_pid[e] for e in linked_fpl}:
        continue
    played = {m: v for m, v in f["min"].items() if v > 0}
    if len(played) < 2 or any(
        i for i in rows("SELECT 1 FROM player_ids WHERE player_id = ? AND source='fpl'", [pid])
    ):
        continue
    cands_summary["fotmob_unlinked_pl_players"] += 1
    hits = [
        e
        for e in unl_fpl
        if fpl_team.get(e) == f["team"]
        and all(abs(fpl_min[e].get(m, -99) - v) <= 5 for m, v in played.items())
    ]
    key = (
        "unique_signature_match"
        if len(hits) == 1
        else ("multiple_signature_matches" if hits else "none")
    )
    cands_summary[key] += 1
    if len(hits) == 1 and len(examples) < 10:
        e = hits[0]
        examples.append(
            {
                "fotmob_name": f["name"],
                "fotmob_pos": f["pos"],
                "fpl_name": fpl_name[e],
                "fpl_pos": fpl_pos.get(e),
                "matches": len(played),
            }
        )
res["unlinked_signature_probe"] = {
    **cands_summary,
    "note": "corroboration by club and per-match minutes only; NOT applied, NOT external truth",
    "examples": rng.sample(examples, min(6, len(examples))),
}

# ---- (2) cup matches traced end to end ------------------------------------------------------
cup = rows(
    "SELECT m.match_id, m.competition_id comp, m.home_goals hg, m.away_goals ag, m.matchday, "
    "(SELECT source_id FROM match_ids i WHERE i.match_id = m.match_id AND i.source='fotmob') fm_id "
    "FROM matches m WHERE m.competition_id <> 'ENG1' AND m.finished"
)
picked = []
for comp in ("UCL", "UEL", "UECL"):
    pool = [c for c in cup if c["comp"] == comp]
    picked += rng.sample(pool, min(2, len(pool)))
traces = []
for c in picked:
    mid = c["match_id"]
    t = rows(
        "SELECT team_id, goals_for FROM team_match WHERE match_id = ? AND source='fotmob' "
        "ORDER BY is_home DESC",
        [mid],
    )
    traces.append(
        {
            **{k: clean(v) for k, v in c.items()},
            "team_match_goals": [x["goals_for"] for x in t],
            "starters_per_team": [
                r["n"]
                for r in rows(
                    "SELECT sum(started::INTEGER) n FROM appearances WHERE match_id = ? GROUP BY team_id",
                    [mid],
                )
            ],
            "shot_goals": rows(
                "SELECT count(*) n FROM shots WHERE match_id = ? AND result='Goal'", [mid]
            )[0]["n"],
            "shot_goals_null_xg_own_goal_proxy": rows(
                "SELECT count(*) n FROM shots WHERE match_id = ? AND result='Goal' AND xg IS NULL",
                [mid],
            )[0]["n"],
            "clubs_map_to_distinct_canonical": len({x["team_id"] for x in t}) == 2,
        }
    )
res["cup_match_trace"] = {
    "sample": traces,
    "matchday_null_by_design": "ingest_fotmob.py: matchday=int(rnd) if rnd.isdigit() and not "
    "league.cup else None",
}

# ---- (3) the FPL/FotMob goal discrepancy ----------------------------------------------------
res["goal_discrepancy"] = {
    "match_id": 40,
    "score": "4-1 (matches table)",
    "fpl_scorers_home_team": "three players with 1 goal each (ids 404, 410, 419) plus one opposing own goal",
    "fotmob_scorers_home_team": "player 709 (1) and player 419 (2) plus the same opposing own goal "
    "(shot with NULL xG)",
    "reading": "team totals agree (4 each way); the sources attribute one goal to different players. "
    "Players 404 and 410 have no FotMob goals, 709 has no FPL id. Retained evidence cannot say "
    "which attribution is right; FPL may have reallocated a goal after the match. UNRESOLVED: "
    "needs an independent match report.",
}

# ---- (4) minutes differences ----------------------------------------------------------------
diffs = rows(
    "WITH f AS (SELECT i.match_id, p.player_id, sum(x.minutes) fm FROM fpl_points_gw x JOIN "
    "match_ids i ON i.source='fpl' AND i.source_id = x.fixture_id::VARCHAR JOIN player_ids p ON "
    "p.source='fpl' AND p.source_id = x.fpl_id::VARCHAR GROUP BY 1, 2) "
    "SELECT a.minutes - f.fm AS d, a.started, a.position, f.fm AS fpl_minutes, a.minutes AS fotmob_minutes, f.match_id, f.player_id "
    "FROM f JOIN appearances a USING (match_id, player_id)"
)
hist = {}
for r in diffs:
    hist[r["d"]] = hist.get(r["d"], 0) + 1
res["minutes_diff"] = {
    "n_compared": len(diffs),
    "histogram_fotmob_minus_fpl": dict(sorted(hist.items())),
    "negative_differences": sum(1 for r in diffs if r["d"] < 0),
    "fotmob_exceeds_fpl_by_1_to_5": sum(1 for r in diffs if 1 <= r["d"] <= 5),
    "gt_5": [{k: clean(v) for k, v in r.items()} for r in diffs if abs(r["d"]) > 5],
    "reading": "FotMob is usually 1 minute above FPL (330 of 1876), equal in 1531, 1 minute below in 13, "
    "and 7 minutes below in 2 (listed in gt_5). FotMob adds stoppage time; the negative cases are "
    "not explained by that and are unresolved (no retained FotMob raw to check substitution times).",
}

# ---- (5) team goals vs player goals ---------------------------------------------------------
res["own_goal_explanation"] = rows(
    "WITH mm AS (SELECT t.match_id, t.team_id FROM team_match t WHERE source='fotmob' AND "
    "goals_for <> coalesce((SELECT sum(s.value) FROM player_match_stats s JOIN appearances a "
    "USING (match_id, player_id) WHERE s.stat='goals' AND a.match_id=t.match_id AND "
    "a.team_id=t.team_id), 0)) "
    "SELECT count(*) mismatching_team_matches, "
    "sum((EXISTS (SELECT 1 FROM shots h WHERE h.match_id = mm.match_id AND h.result='Goal' "
    "AND h.xg IS NULL))::INTEGER) with_null_xg_goal_shot_in_match FROM mm"
)[0]
res["null_xg_shots"] = rows("SELECT result, count(*) n FROM shots WHERE xg IS NULL GROUP BY 1")
res["shots_xg_vs_team_xg"] = rows(
    "SELECT count(*) matches, sum((abs(sx - tx) > 0.05)::INTEGER) differ, "
    "sum((abs(sx - tx) > 0.05 AND nn = 0)::INTEGER) differ_without_null_xg FROM (SELECT "
    "s.match_id, sum(s.xg) sx, (SELECT sum(xg_for) FROM team_match t WHERE t.match_id = s.match_id "
    "AND t.source='fotmob') tx, sum((s.xg IS NULL)::INTEGER) nn FROM shots s GROUP BY s.match_id)"
)[0]
res["null_position_appearance"] = rows(
    "SELECT m.competition_id comp, a.minutes, a.started FROM appearances a JOIN matches m USING "
    "(match_id) WHERE a.position IS NULL"
)

# ---- (6) dispositions of the six failed checks ---------------------------------------------
res["failed_check_dispositions"] = [
    {
        "id": "null.matches.matchday",
        "n": "396/776",
        "disposition": "valid by design",
        "why": "cup matches have no round number (ingest_fotmob.py sets matchday only for league "
        "rounds). Consumers must key cups by date; not a production failure.",
    },
    {
        "id": "null.runs.gw",
        "n": "1/5",
        "disposition": "valid",
        "why": "the run failed at bootstrap (SourceUnavailable) before a gameweek was known.",
    },
    {
        "id": "null.appearances.position",
        "n": "1/6862",
        "disposition": "benign data gap",
        "why": "an unused UEL substitute (0 minutes, no stats) whose position FotMob did not give.",
    },
    {
        "id": "null.shots.xg",
        "n": "15/4603",
        "disposition": "proxy-explained (unconfirmed), needs contract",
        "why": "all 15 are result='Goal' and each sits in a match whose team goals exceed player "
        "goals: an own-goal PROXY, not independent proof of attribution. NULL means 'no xG supplied', so xG sums exclude them; the "
        "contract for own goals must be explicit.",
    },
    {
        "id": "domain.matches.stale_unfinished_past_kickoff",
        "n": "16/776",
        "disposition": "stale-state observation, not corruption",
        "why": "16 UCL matches kicked off 2026-10-20 19:00Z; the last fetch (20:09Z) preceded full "
        "time. Reflects data age, not a wrong value.",
    },
    {
        "id": "recon.fpl_vs_fotmob.goals",
        "n": "1/1324",
        "disposition": "UNRESOLVED",
        "why": "see goal_discrepancy; attribution conflict between sources, external truth needed.",
    },
]

# ---- (7) ownership semantics ----------------------------------------------------------------
res["ownership_semantics"] = {
    "eo_below_owned": rows("SELECT count(*) n FROM field_ownership WHERE eo < owned")[0]["n"],
    "formula": "ownership.py:39 eo counts pick multipliers (bench 0, captain 2), so eo < owned is "
    "expected for benched players",
    "temporal": "the row labelled GW g holds the picks for GW g-1 (ownership.py:85-100, last "
    "deadline's picks); it is a sample of 200 from the top 10,000, not population ownership",
    "absent_rows": "512 of 668 players have no row = not in the sample (unknown), not 0%",
    "bench_check": "no per-pick data retained, so the bench explanation is not verified here",
}

(OUT / "semantic_sampling.json").write_text(
    json.dumps(res, indent=1, default=str), encoding="utf-8"
)
print(
    json.dumps(
        {
            k: res[k]
            for k in (
                "linked_population_agreement",
                "refused_position_pairs",
                "unlinked_signature_probe",
                "own_goal_explanation",
                "shots_xg_vs_team_xg",
                "null_xg_shots",
            )
        },
        indent=1,
        default=str,
    )[:3500]
)
print(json.dumps(res["minutes_diff"], default=str)[:900])
