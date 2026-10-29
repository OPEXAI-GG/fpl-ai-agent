# ruff: noqa: E501
"""Reconcile the DB01 coverage ledger (artifacts/db-audit-20261022/coverage.csv, 237 columns of the
then-production schema) with the CURRENT declared schema, and gate the model-input contracts.

1. Every current declared column must be in the original ledger or be classified below (NEW);
   columns the ledger has that the schema no longer declares are reported.
2. Every column a model/feature consumer reads (MODEL_INPUTS) must be validated by a production
   quality check that fires on corrupted data (its name is a `test_corruption_is_detected` case
   in tests/test_quality.py and appears in quality.py), or by a named regression test (temporal
   and provenance contracts), or carry an explicit exclusion/degraded statement. References
   alone never count.

Writes coverage-current.csv and ledger-current.json next to itself. Exit 0 = reconciled and
every model input resolved; 1 = something unresolved; 2 = an input is missing.
"""

import csv
import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).parent
ROOT = HERE.parents[2]
sys.path.insert(0, str(ROOT / "src"))
from fpl_agent.db.database import declared_types  # noqa: E402

try:
    old = list(
        csv.DictReader(open(ROOT / "artifacts/db-audit-20261022/coverage.csv", encoding="utf-8"))
    )
    qsrc = (ROOT / "src/fpl_agent/db/quality.py").read_text(encoding="utf-8")
    tq = (ROOT / "tests/test_quality.py").read_text(encoding="utf-8")
    tests_text = "\n".join(
        p.read_text(encoding="utf-8") for p in (ROOT / "tests").glob("test_*.py")
    )
except OSError as e:
    print(f"INPUT ERROR: {e}", file=sys.stderr)
    raise SystemExit(2) from None

# columns of tables added after the DB01 audit, with their classification and evidence
NEW = {
    "player_codes": (
        "identity evidence (R1)",
        "validated by R1 tests: test_fpl_identity*.py (code collisions refused, journal roundtrip); exported/restored exactly (test_a_full_restore_reproduces_every_current_table_exactly)",
    ),
    "migration_log": (
        "operational journal (R1 relabel)",
        "rollback tests in test_fpl_identity*.py; exact restore test",
    ),
    "repair_log": (
        "operational journal (R2)",
        "test_quality rollback tests (exact restore, conflicts, repeat no-op); exact restore test",
    ),
    "source_attempts": (
        "operational record (U05)",
        "test_pipeline attempt tests; source_health tests (test_source_health.py); exact restore test",
    ),
    "fpl_deadlines": (
        "versioned observation (U06)",
        "test_point_in_time / ingest deadline tests; eligibility tests (official-before-deadline rule)",
    ),
}

# (table, column) -> (consumer, how it is validated)
#   check:<name>  a production run_checks check proven to fire by a corruption test
#   test:<name>   a named regression test (temporal / provenance / key contracts)
#   degraded:...  an explicit statement of what the consumer does with unvalidated values
MODEL_INPUTS = {
    ("fpl_points_gw", "player_id"): (
        "features.player_gw_frame",
        ["check:fpl_points_unlinked", "check:orphan_fpl_points_gw.player_id"],
    ),
    ("fpl_points_gw", "fpl_id"): (
        "features.player_gw_frame (snapshot join)",
        ["check:duplicate_player_fixture"],
    ),
    ("fpl_points_gw", "fixture_id"): (
        "features.player_gw_frame",
        ["check:duplicate_player_fixture"],
    ),
    ("fpl_points_gw", "kickoff"): (
        "project.build_projection cutoff filter",
        ["check:fpl_points_without_kickoff"],
    ),
    ("fpl_points_gw", "minutes"): ("minutes model", ["check:bad_fpl_minutes"]),
    ("fpl_points_gw", "total_points"): ("backtest/replay scoring", ["check:points_vs_rules"]),
    ("fpl_points_gw", "stats"): ("features STAT_FIELDS", ["check:points_vs_rules"]),
    ("fpl_points_gw", "bps"): ("project.fit_bps_noise", ["check:bad_bps"]),
    ("fpl_points_gw", "bonus"): ("features (bonus)", ["check:points_vs_rules"]),
    ("fpl_players_gw", "snapshot_at"): (
        "features.snapshot_as_of / current_pool cutoff",
        [
            "test:test_own_gameweek_snapshots_are_selected_as_of_the_cutoff",
            "test:test_late_snapshots_of_past_gameweeks_cannot_change_the_history_a_projection_uses",
        ],
    ),
    ("fpl_players_gw", "raw"): (
        "features.snapshot_kind / eligibility provenance",
        [
            "test:test_history_rows_say_whether_their_snapshot_was_observed_or_reconstructed",
            "test:test_only_inputs_observed_before_an_officially_recorded_deadline_are_eligible",
        ],
    ),
    ("fpl_players_gw", "position"): ("features / pool", ["check:bad_position"]),
    ("fpl_players_gw", "now_cost"): ("minutes model price features / pool", ["check:bad_prices"]),
    ("fpl_players_gw", "status"): (
        "availability",
        [
            "check:bad_status",
            "degraded:archive rows assume status 'a'; they are labelled reconstructed and excluded from eligible gameweeks",
        ],
    ),
    ("fpl_players_gw", "chance_next"): ("availability", ["check:bad_chance"]),
    ("fpl_players_gw", "team_id"): (
        "pool / price_vs_best",
        ["check:orphan_fpl_players_gw.team_id"],
    ),
    ("fpl_players_gw", "player_id"): (
        "pool",
        ["check:orphan_fpl_players_gw.player_id", "check:duplicate_player_snapshot"],
    ),
    ("matches", "home_team_id"): (
        "fixtures_for / strength",
        ["check:orphan_matches.home_team_id", "check:same_team_match"],
    ),
    ("matches", "away_team_id"): ("fixtures_for / strength", ["check:same_team_match"]),
    ("matches", "home_goals"): ("strength", ["check:finished_without_score"]),
    ("matches", "away_goals"): ("strength", ["check:finished_without_score"]),
    ("matches", "match_date"): ("strength.load_matches as_of", ["check:finished_without_date"]),
    ("matches", "kickoff"): (
        "fixtures_for (per-player first kickoff)",
        [
            "check:scheduled_without_kickoff",
            "degraded:warn only: FPL leaves kickoff empty for fixtures it has not placed in a gameweek (matchday NULL), which fixtures_for never selects; 0 scheduled fixtures without kickoff in the snapshot copies",
        ],
    ),
    ("matches", "matchday"): (
        "fixtures_for",
        [
            "degraded:a fixture without a matchday is not selected for any gameweek (blank), never mis-assigned"
        ],
    ),
    ("team_match", "goals_for"): ("strength", ["check:negative_or_huge_goals"]),
    ("team_match", "xg_for"): ("strength / player shares", ["check:bad_xg"]),
    ("team_match", "team_id"): ("match_frame", ["check:orphan_team_match.team_id"]),
    ("appearances", "minutes"): ("match_frame", ["check:bad_minutes"]),
    ("appearances", "position"): ("players.position_priors", ["check:bad_appearance_position"]),
    ("appearances", "player_id"): ("match_frame", ["check:orphan_appearances.player_id"]),
    ("appearances", "team_id"): ("match_frame", ["check:orphan_appearances.team_id"]),
    ("appearances", "match_id"): ("match_frame", ["check:orphan_appearances.match_id"]),
    ("player_match_stats", "value"): ("match_frame", ["check:negative_match_stat"]),
    ("player_match_stats", "stat"): (
        "match_frame",
        [
            "degraded:match_frame pivots only the names in MATCH_STATS; any other stat name is ignored, never misread"
        ],
    ),
    ("player_match_stats", "match_id"): (
        "match_frame",
        ["check:orphan_player_match_stats.match_id"],
    ),
    ("match_odds", "home"): ("project.market_lambdas", ["check:odds_below_one"]),
    ("match_odds", "over25"): ("project.market_lambdas", ["check:odds_below_one"]),
    ("match_odds", "match_id"): ("project.market_lambdas", ["check:orphan_match_odds.match_id"]),
    ("match_odds_log", "home"): (
        "project.market_lambdas with a cutoff",
        ["check:odds_log_below_one"],
    ),
    ("match_odds_log", "match_id"): (
        "project.market_lambdas with a cutoff",
        ["check:orphan_match_odds_log.match_id"],
    ),
    ("match_odds_log", "ingested_at"): (
        "cutoff selection",
        [
            "test:test_data_from_after_the_cutoff_cannot_change_a_projection",
            "test:test_prices_known_before_the_cutoff_are_used",
        ],
    ),
    ("league_translation", "factor"): ("translation.Translations", ["check:bad_translation"]),
    ("league_translation", "updated_at"): (
        "Translations.load(as_of)",
        ["test:test_translation_fits_are_loaded_as_of_the_cutoff"],
    ),
    ("competitions", "tier"): (
        "strength",
        [
            "degraded:a NULL tier is read as 1 (top flight) by strength.load_matches; documented default, values seen 0-3"
        ],
    ),
}

corruption_ids = set(
    re.findall(
        r'^\s*\("([^"]+)",', tq.split("CORRUPTIONS = [", 1)[1].split("]  # fmt: skip", 1)[0], re.M
    )
)
schema = declared_types()
current = {(t, c) for t, cols in schema.items() for c, _ in cols}
ledger = {(r["table"], r["column"]): r for r in old}

problems, rows = [], []
for t, cols in schema.items():
    for c, ty in cols:
        r = ledger.get((t, c))
        if r:
            outcome, evidence = (
                "in DB01 ledger",
                r.get("validated_dimensions") or r.get("audit_outcome") or "",
            )
        elif t in NEW:
            outcome, evidence = NEW[t]
        else:
            outcome, evidence = "UNRESOLVED", ""
            problems.append(f"{t}.{c}: not in the DB01 ledger and not classified")
        consumer, how = MODEL_INPUTS.get((t, c), ("", []))
        status = ""
        if how:
            bad = []
            for h in how:
                kind, _, name = h.partition(":")
                if kind == "check" and not (
                    name in corruption_ids
                    and (name.split(".")[0] in qsrc or name.startswith("orphan_"))
                ):
                    bad.append(f"check {name} has no corruption test")
                elif kind == "test" and f"def {name}(" not in tests_text:
                    bad.append(f"test {name} not found")
                elif kind not in ("check", "test", "degraded"):
                    bad.append(f"unknown kind {h}")
            status = "; ".join(bad) or "resolved"
            problems += [f"{t}.{c}: {b}" for b in bad]
        rows.append(
            {
                "table": t,
                "column": c,
                "type": ty,
                "ledger": outcome,
                "evidence": evidence,
                "model_consumer": consumer,
                "model_contract": " | ".join(how),
                "model_status": status,
            }
        )
problems += [
    f"{t}.{c}: model input not in the declared schema"
    for t, c in MODEL_INPUTS
    if (t, c) not in current
]
gone = sorted(f"{t}.{c}" for t, c in ledger if (t, c) not in current)

with open(HERE / "coverage-current.csv", "w", newline="", encoding="utf-8") as f:
    w = csv.DictWriter(f, fieldnames=list(rows[0]))
    w.writeheader()
    w.writerows(rows)
summary = {
    "declared_columns": len(current),
    "db01_ledger_columns": len(ledger),
    "carried_from_db01": sum(r["ledger"] == "in DB01 ledger" for r in rows),
    "new_classified": sum(r["table"] in NEW for r in rows),
    "ledger_columns_no_longer_declared": gone,
    "model_inputs": len(MODEL_INPUTS),
    "model_inputs_by_check": sum(
        any(h.startswith("check:") for h in v[1]) for v in MODEL_INPUTS.values()
    ),
    "model_inputs_by_test_only": sum(
        all(h.startswith("test:") for h in v[1]) for v in MODEL_INPUTS.values()
    ),
    "model_inputs_degraded_only": sorted(
        f"{t}.{c}"
        for (t, c), v in MODEL_INPUTS.items()
        if all(h.startswith("degraded:") for h in v[1])
    ),
    "corruption_cases": len(corruption_ids),
    "problems": problems,
    "ok": not problems,
}
(HERE / "ledger-current.json").write_text(json.dumps(summary, indent=1), encoding="utf-8")
print(json.dumps(summary, indent=1))
raise SystemExit(0 if not problems else 1)
