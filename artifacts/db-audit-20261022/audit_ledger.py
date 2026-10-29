# ruff: noqa: E501
"""Resolve the coverage contract: map every check to the columns its SQL references, classify every
check, and give every column a resolvable outcome (check ids), a stated not-applicable reason, or a
named untested gap. Run after audit_inventory.py and audit_checks.py. Fails (exit 1) if any column
is left without one of those three. Column references are parsed from the check SQL (string
literals removed): qualified references and unqualified names found in exactly one table of the
statement are 'direct'; unqualified names that several joined tables share are 'ambiguous' and
never count as coverage.

Reference is not validation. Each check id prefix names the dimension it tests (null=presence,
domain/numeric/enum/json=value contract, orphan=relation, key=uniqueness, recon/time/identity/
contract=cross-source or temporal). A column counts as validated for a dimension only when it appears
in a check's violation predicate SQL; appearing only in a denominator, or only in a `key` check
(grouping), is recorded as referenced, not validated. Dimensions with no predicate check are listed
as not assessed.

Exit codes: 0 = audit complete (data findings are allowed and reported, they are results);
1 = audit incomplete or broken (untested gap, unclassified/errored check, invalid check reference,
schema drift or failed ledger reconciliation in inventory.json); 2 = required input missing or
unreadable. `--dir PATH` runs against another artifact folder (used by test_ledger_gate.py)."""

import argparse
import csv
import json
import re
import sys
from pathlib import Path

_ap = argparse.ArgumentParser()
_ap.add_argument("--dir", default=str(Path(__file__).parent))
OUT = Path(_ap.parse_args().dir)
try:
    checks = json.loads((OUT / "checks.json").read_text(encoding="utf-8"))
    inv = json.loads((OUT / "inventory.json").read_text(encoding="utf-8"))
    csv.DictReader(open(OUT / "coverage.csv", encoding="utf-8"))
except (OSError, ValueError) as e:
    print(f"INPUT ERROR: {type(e).__name__}: {e}", file=sys.stderr)
    raise SystemExit(2) from None
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
    "null.shots.xg": "F-06 explained: all 15 are Goal shots with NULL xG in matches whose team goals exceed player goals: an own-goal PROXY, not independently confirmed; contract needed",
    "domain.matches.stale_unfinished_past_kickoff": "F-11 stale-state observation (16 UCL matches in play at last fetch)",
    "recon.fpl_vs_fotmob.goals": "F-16 UNRESOLVED attribution conflict (match 40)",
    "recon.shots.xg_vs_team_xg": "F-16 unexplained: 26 of 162 differ without NULL xG",
    "recon.team_match.xg_understat_vs_fotmob": "F-16 two xG models differ (expected, not a defect)",
    "recon.stats.goals_vs_team_goals": "F-06 proxy: 15/15 coincide with a NULL-xG Goal shot (own-goal proxy, unconfirmed)",
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
DIM = {
    "null": "presence",
    "domain": "value contract",
    "numeric": "value contract",
    "enum": "value contract",
    "json": "value contract",
    "contract": "value contract",
    "orphan": "relation",
    "key": "uniqueness",
    "recon": "cross-source",
    "identity": "cross-source",
    "time": "temporal",
}
STAGE = {
    "presence": "DB03 null contract",
    "value contract": "DB03/DB04 domain contract",
    "relation": "DB04 relational integrity",
    "cross-source": "DB06/DB07 reconciliation",
    "temporal": "DB05/R3 point-in-time",
}
# checks that interrogate the catalog itself, so they legitimately reference no table column
CATALOG_PROBES = {"time.no_deadline_column"}
bad_refs: list[str] = []
seen_ids: set[str] = set()
for c in checks["checks"]:
    cid = c.get("id", "?")
    if cid in seen_ids or cid.split(".")[0] not in DIM:
        bad_refs.append(f"{cid}: duplicate id or unknown dimension prefix")
    seen_ids.add(cid)
    st = (c.get("result") or {}).get("status")
    if st not in ("pass", "fail", "info"):
        bad_refs.append(f"{cid}: status {st!r} (errored or missing result)")
    pred, a = refs(c.get("violations_sql", ""))
    dend, _ = refs(c.get("denominator_sql", ""))
    c["columns"] = sorted(pred | dend)
    c["columns_predicate"] = sorted(pred)
    c["columns_ambiguous"] = sorted(a)
    if not (pred | dend | a) and cid not in CATALOG_PROBES:
        bad_refs.append(f"{cid}: SQL references no live table column")
    if cid in FINDING:
        c["classification"] = FINDING[cid]
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
live_cols = {f"{t}.{c}" for t, cs_ in live.items() for c in cs_}
csv_cols = [f"{r['table']}.{r['column']}" for r in rows]
bad_refs += [f"coverage.csv row for unknown column {k}" for k in csv_cols if k not in live_cols]
bad_refs += [
    f"live column {k} missing from coverage.csv" for k in sorted(live_cols - set(csv_cols))
]
by_col: dict[str, list[dict]] = {}
pred_col: dict[str, list[dict]] = {}
for c in checks["checks"]:
    for col in c["columns"]:
        by_col.setdefault(col, []).append(c)
    for col in c["columns_predicate"]:
        pred_col.setdefault(col, []).append(c)
amb: dict[str, set[str]] = {}
for c in checks["checks"]:
    for col in c["columns_ambiguous"]:
        amb.setdefault(col, set()).add(c["id"])
unresolved = []
for r in rows:
    key = f"{r['table']}.{r['column']}"
    cs = by_col.get(key, [])
    ps = pred_col.get(key, [])
    ids = [c["id"] for c in cs]
    r.pop("existing_checks", None)
    r.pop("audit_outcome", None)
    r["check_ids"] = ";".join(ids)
    r["predicate_check_ids"] = ";".join(c["id"] for c in ps)
    r["ambiguous_check_ids"] = ";".join(sorted(amb.get(key, [])))
    # validated dimensions: predicate references, excluding uniqueness (grouping) checks
    val = sorted({DIM.get(c["id"].split(".")[0], "?") for c in ps} - {"uniqueness"})
    r["validated_dimensions"] = ";".join(val)
    miss = [d for d in ("presence", "value contract", "relation", "temporal") if d not in val]
    r["not_assessed_dimensions"] = ";".join(miss)
    r["not_assessed_owner_stage"] = ";".join(f"{d}: Claude / {STAGE[d]}" for d in miss)
    na = NA.get((r["table"], "*")) or NA.get((r["table"], r["column"]))
    stat = [c["result"]["status"] for c in ps]
    if r["table_rows"] == "0":
        out = "not testable: table empty (0 rows), any check is vacuous" + (
            f"; {ids and len(ids)} checks reference it" if ids else ""
        )
    elif ps and "fail" in stat:
        out = "finding: " + "; ".join(
            c["classification"] for c in ps if c["result"]["status"] == "fail"
        )
    elif ps and "pass" in stat and val:
        out = (
            f"checked: pass ({stat.count('pass')} predicate checks; validated: {', '.join(val)})"
            + (f"; also measured by {stat.count('info')} info checks" if "info" in stat else "")
        )
    elif ps and "pass" in stat:
        out = "referenced only: uniqueness/grouping check passes; no value contract validated"
    elif ps:
        out = "measured only (info checks; no pass/fail rule)"
    elif cs:
        out = "referenced only: appears in denominator/context SQL; no predicate validates it"
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
drift = list(inv.get("schema_drift_vs_declared") or [])
recon_ok = bool((inv.get("ledger_reconciliation") or {}).get("ok", True))


def n(prefix):
    return sum(r["audit_outcome"].startswith(prefix) for r in rows)


summary = {
    "columns": len(rows),
    "checked_pass": n("checked"),
    "finding": n("finding"),
    "measured_only": n("measured"),
    "referenced_only_not_validated": n("referenced only"),
    "not_applicable": n("not applicable"),
    "not_testable_empty_table": n("not testable"),
    "untested_gap": len(unresolved),
    "unclassified_checks": len(unclassified),
    "invalid_references": len(bad_refs),
    "schema_drift": len(drift),
    # same denominator as the next key: columns that are testable and applicable (nonempty table)
    "applicable_testable_columns": sum(
        not r["audit_outcome"].startswith(("not testable", "not applicable")) for r in rows
    ),
    "columns_with_value_contract_validated": sum(
        "value contract" in r["validated_dimensions"]
        and not r["audit_outcome"].startswith(("not testable", "not applicable"))
        for r in rows
    ),
    "columns_without_value_contract_validated_and_testable": sum(
        "value contract" not in r["validated_dimensions"]
        and not r["audit_outcome"].startswith(("not testable", "not applicable"))
        for r in rows
    ),
}
failures = {
    "untested_gap_columns": unresolved,
    "unclassified": unclassified,
    "invalid_references": bad_refs,
    "schema_drift": drift,
    "ledger_reconciliation_ok": recon_ok,
}
complete = not (unresolved or unclassified or bad_refs or drift) and recon_ok
(OUT / "ledger_summary.json").write_text(
    json.dumps({**summary, **failures, "audit_complete": complete}, indent=1), encoding="utf-8"
)
print(json.dumps(summary))
print("untested gaps:", unresolved)
print("unclassified:", unclassified)
print("invalid references:", bad_refs)
print("schema drift:", drift, "| reconciliation ok:", recon_ok)
print("AUDIT COMPLETE" if complete else "AUDIT INCOMPLETE")
raise SystemExit(0 if complete else 1)
