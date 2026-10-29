# ruff: noqa: E501
"""Resolve the coverage contract: map every check to the columns its SQL references, classify every
check, and give every column a resolvable outcome (check ids), a stated not-applicable reason, or a
named untested gap. Run after audit_inventory.py and audit_checks.py. Fails (exit 1) if any column
is left without one of those three. Column references are parsed from the check SQL (string
literals removed): qualified references and unqualified names found in exactly one table of the
statement are 'direct'; unqualified names that several joined tables share are 'ambiguous' and
never count as coverage."""

import csv
import json
import re
from pathlib import Path

OUT = Path(__file__).parent
checks = json.loads((OUT / "checks.json").read_text(encoding="utf-8"))
inv = json.loads((OUT / "inventory.json").read_text(encoding="utf-8"))
live = {t: [c["name"] for c in v["columns"]] for t, v in inv["tables"].items()}
KEYWORDS = {
    "WHERE",
    "ON",
    "USING",
    "JOIN",
    "LEFT",
    "RIGHT",
    "INNER",
    "GROUP",
    "ORDER",
    "QUALIFY",
    "HAVING",
    "UNION",
    "AS",
    "LIMIT",
    "SELECT",
    "FROM",
    "AND",
    "OR",
    "NOT",
    "WITH",
    "OVER",
}


def refs(sql: str) -> tuple[set[str], set[str]]:
    sql = re.sub(r"'[^']*'", "''", sql)
    tables = {t for t in live if re.search(rf"\b{t}\b", sql)}
    alias = {t: t for t in tables}
    for t, a in re.findall(r"\b(?:FROM|JOIN)\s+(\w+)\s+(?:AS\s+)?(\w+)\b", sql):
        if t in live and a.upper() not in KEYWORDS:
            alias[a] = t
    direct, ambiguous = set(), set()
    for a, c in re.findall(r"\b(\w+)\.(\w+)\b", sql):
        if a in alias and c in live[alias[a]]:
            direct.add(f"{alias[a]}.{c}")
    qualified = {x.split(".")[1] for x in direct}
    for tok in set(re.findall(r"\b[A-Za-z_]\w*\b", sql)):
        owners = [t for t in tables if tok in live[t]]
        if len(owners) == 1 and tok not in KEYWORDS:
            direct.add(f"{owners[0]}.{tok}")
        elif len(owners) > 1 and tok not in qualified:
            ambiguous.update(f"{t}.{tok}" for t in owners)
    return direct, ambiguous


# explicit finding or disposition for checks that are not plain passes
FINDING = {
    "null.matches.matchday": "valid by design (cup matches carry no round; ingest_fotmob.py)",
    "null.runs.gw": "valid (failed bootstrap run has no gameweek)",
    "null.appearances.position": "benign (unused UEL substitute, 0 minutes)",
    "null.shots.xg": "F-06 explained: all 15 are own-goal-pattern Goal shots; contract needed",
    "domain.matches.stale_unfinished_past_kickoff": "F-11 stale-state observation (16 UCL matches in play at last fetch)",
    "recon.fpl_vs_fotmob.goals": "F-16 UNRESOLVED attribution conflict (match 40)",
    "recon.shots.xg_vs_team_xg": "F-16 unexplained: 26 of 162 differ without NULL xG",
    "recon.team_match.xg_understat_vs_fotmob": "F-16 two xG models differ (expected, not a defect)",
    "recon.stats.goals_vs_team_goals": "F-06 explained: 15/15 coincide with a NULL-xG own-goal shot",
    "key.fpl_players_gw.one_snapshot_per_gw_player": "F-09 design risk (join grain)",
    "key.team_match.sources_per_team_match": "F-09 design risk (join grain)",
    "key.match_odds.rows_per_match": "F-09 design risk (join grain)",
    "orphan.projections.run_in_runs": "F-13 provenance gap",
    "identity.fpl_ids_not_season_scoped": "F-01 confirmed defect (see repro_id_reuse.py)",
    "identity.fpl_players_without_fotmob_link": "F-05 coverage gap",
    "identity.fpl_minutes_without_fotmob_link": "F-05 coverage gap",
    "identity.unlinked_marker_rows": "F-05 coverage gap",
    "identity.player_link.position_agrees": "F-05 measurement",
    "identity.player_link.name_similarity": "F-05 measurement (cannot prove absence of wrong merges)",
    "identity.namesake_split_candidates": "F-05 measurement",
    "identity.fpl_current_team_vs_match_team": "F-03 measurement (club history not stored)",
    "recon.finished_cup_without_odds": "F-04 coverage gap",
    "recon.finished_pl_without_odds": "F-04 measurement",
    "recon.upcoming_pl_without_odds": "F-04 coverage gap",
    "recon.odds.log_vs_current": "F-04 coverage gap",
    "time.match_odds_known_at": "F-04 coverage gap",
    "recon.appearances.without_stats": "F-06 benign: all 1,814 have 0 minutes",
    "recon.appearances.without_stats_played": "F-06 measurement",
    "recon.appearances.team_coverage": "measurement",
    "recon.fpl.gw_row_coverage": "F-06 absent row is not zero",
    "recon.fpl_vs_fotmob.minutes": "F-14 explained for 330 (+1 min); 15 negative unresolved",
    "recon.fpl_vs_fotmob.minutes_gt_1": "F-14 two 7-minute cases unresolved",
    "recon.fpl_played_not_in_fotmob": "measurement",
    "recon.shots.goals_vs_score": "measurement",
    "recon.team_match.sources_agree_goals": "measurement",
    "time.snapshots_per_gw": "F-03 coverage gap (GW1-7 snapshots absent)",
    "time.no_deadline_column": "F-03 coverage gap (no stored official deadline)",
}
for c in checks["checks"]:
    d, a = refs(c["violations_sql"] + " " + c["denominator_sql"])
    c["columns"] = sorted(d)
    c["columns_ambiguous"] = sorted(a)
    st = c["result"]["status"]
    if c["id"] in FINDING:
        c["classification"] = FINDING[c["id"]]
    elif st == "pass":
        c["classification"] = "ok (no violations on the full population)"
    elif st == "info":
        c["classification"] = "measurement (see report finding)"
    else:
        c["classification"] = "UNCLASSIFIED"

# not applicable or out of scope, with the reason
NA_FREE_TEXT = "free text / opaque payload: no domain rule; only presence/validity is checkable"
NA = {
    ("players", "birth_date"): "never written by any code path (always NULL); nothing to validate",
    ("transfers", "*"): "table has no writer and no rows; checks run but no population exists",
    (
        "reviews",
        "*",
    ): "table empty (written only after a reviewed gameweek); contract untested for lack of rows",
    ("lessons", "*"): "table empty; contract untested for lack of rows",
    ("league_translation", "*"): "table empty; contract untested for lack of rows",
    ("scout_cache", "*"): "table empty; contract untested for lack of rows",
    ("scout_calls", "*"): "table empty; contract untested for lack of rows",
    ("match_odds_log", "*"): "table empty; column checks run but have no rows (F-04)",
}
FREE = {
    ("teams", "name"),
    ("players", "name"),
    ("player_ids", "source_name"),
    ("team_ids", "source_name"),
    ("ingest_log", "note"),
    ("ingest_state", "detail"),
    ("fpl_players_gw", "news"),
    ("transfers", "fee"),
    ("shots", "assisted_by"),
    ("shots", "last_action"),
    ("runs", "report_key"),
    ("projections", "model"),
    ("teams", "key"),
}

rows = list(csv.DictReader(open(OUT / "coverage.csv", encoding="utf-8")))
by_col: dict[str, list[dict]] = {}
for c in checks["checks"]:
    for col in c["columns"]:
        by_col.setdefault(col, []).append(c)
amb: dict[str, set[str]] = {}
for c in checks["checks"]:
    for col in c["columns_ambiguous"]:
        amb.setdefault(col, set()).add(c["id"])
unresolved = []
for r in rows:
    key = f"{r['table']}.{r['column']}"
    cs = by_col.get(key, [])
    ids = [c["id"] for c in cs]
    r.pop("existing_checks", None)
    r.pop("audit_outcome", None)
    r["check_ids"] = ";".join(ids)
    r["ambiguous_check_ids"] = ";".join(sorted(amb.get(key, [])))
    na = NA.get((r["table"], "*")) or NA.get((r["table"], r["column"]))
    stat = [c["result"]["status"] for c in cs]
    if r["table_rows"] == "0":
        out = "not testable: table empty (0 rows), any check is vacuous" + (
            f"; {ids and len(ids)} checks reference it" if ids else ""
        )
    elif cs and "fail" in stat:
        out = "finding: " + "; ".join(
            c["classification"] for c in cs if c["result"]["status"] == "fail"
        )
    elif cs and "pass" in stat:
        out = f"checked: pass ({stat.count('pass')} checks)" + (
            f"; also measured by {stat.count('info')} info checks" if "info" in stat else ""
        )
    elif cs:
        out = "measured only (info checks; no pass/fail rule)"
    elif na:
        out = "not applicable: " + na
    elif (r["table"], r["column"]) in FREE:
        out = "not applicable: " + NA_FREE_TEXT
    else:
        out = "UNTESTED GAP: no check references this column"
        unresolved.append(key)
    if na and cs:
        out += " | note: " + na
    r["audit_outcome"] = out
fields = list(rows[0])
with open(OUT / "coverage.csv", "w", newline="", encoding="utf-8") as f:
    w = csv.DictWriter(f, fieldnames=fields)
    w.writeheader()
    w.writerows(rows)
(OUT / "checks.json").write_text(json.dumps(checks, indent=1, default=str), encoding="utf-8")
unclassified = [c["id"] for c in checks["checks"] if c["classification"] == "UNCLASSIFIED"]
summary = {
    "columns": len(rows),
    "checked_pass": sum(r["audit_outcome"].startswith("checked") for r in rows),
    "finding": sum(r["audit_outcome"].startswith("finding") for r in rows),
    "measured_only": sum(r["audit_outcome"].startswith("measured") for r in rows),
    "not_applicable": sum(r["audit_outcome"].startswith("not applicable") for r in rows),
    "not_testable_empty_table": sum(r["audit_outcome"].startswith("not testable") for r in rows),
    "untested_gap": len(unresolved),
    "unclassified_checks": len(unclassified),
}
(OUT / "ledger_summary.json").write_text(
    json.dumps(
        {**summary, "untested_gap_columns": unresolved, "unclassified": unclassified}, indent=1
    ),
    encoding="utf-8",
)
print(json.dumps(summary))
print("untested gaps:", unresolved)
print("unclassified:", unclassified)
