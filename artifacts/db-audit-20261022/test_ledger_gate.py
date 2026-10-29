"""Exit-code tests for audit_ledger.py on isolated copies of the artifact (never the originals).
Run from the repo root: python -m pytest artifacts/db-audit-20261022/test_ledger_gate.py"""

import copy
import csv
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).parent
INPUTS = ("checks.json", "inventory.json", "coverage.csv")


@pytest.fixture
def fx(tmp_path):
    for f in INPUTS:
        shutil.copy(HERE / f, tmp_path / f)
    return tmp_path


def run(d):
    p = subprocess.run(
        [sys.executable, str(HERE / "audit_ledger.py"), "--dir", str(d)],
        capture_output=True,
        text=True,
    )
    return p.returncode, p.stdout + p.stderr


def edit_json(d, name, fn):
    path = d / name
    obj = json.loads(path.read_text(encoding="utf-8"))
    fn(obj)
    path.write_text(json.dumps(obj), encoding="utf-8")


def test_clean_artifact_with_documented_findings_exits_0(fx):
    rc, out = run(fx)
    assert rc == 0, out
    assert "AUDIT COMPLETE" in out
    checks = json.loads((fx / "checks.json").read_text(encoding="utf-8"))["checks"]
    assert sum(c["result"]["status"] == "fail" for c in checks) == 6  # data findings are not errors


def test_injected_uncovered_column_exits_1(fx):
    def add(inv):
        t = inv["tables"]["appearances"]
        t["columns"].append({**copy.deepcopy(t["columns"][0]), "name": "probe_uncovered"})

    edit_json(fx, "inventory.json", add)
    rows = list(csv.DictReader(open(fx / "coverage.csv", encoding="utf-8")))
    probe = next(r for r in rows if r["table"] == "appearances")
    probe = {**probe, "column": "probe_uncovered"}
    rows.append(probe)
    with open(fx / "coverage.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)
    rc, out = run(fx)
    assert rc == 1, out
    assert "appearances.probe_uncovered" in out and "AUDIT INCOMPLETE" in out


def test_column_missing_from_ledger_exits_1(fx):
    rows = list(csv.DictReader(open(fx / "coverage.csv", encoding="utf-8")))
    rows = rows[1:]
    with open(fx / "coverage.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)
    rc, out = run(fx)
    assert rc == 1 and "missing from coverage.csv" in out, out


def test_unclassified_check_exits_1(fx):
    def drop(ch):
        c = next(x for x in ch["checks"] if x["result"]["status"] == "fail")
        c["id"] = "domain.probe.unclassified_failure"  # a failure with no documented disposition

    edit_json(fx, "checks.json", drop)
    rc, out = run(fx)
    assert rc == 1 and "domain.probe.unclassified_failure" in out, out


def test_errored_check_exits_1(fx):
    def err(ch):
        ch["checks"][0]["result"] = {"status": "error", "error": "boom"}

    edit_json(fx, "checks.json", err)
    rc, out = run(fx)
    assert rc == 1 and "errored or missing result" in out, out


def test_check_referencing_nothing_exits_1(fx):
    def blank(ch):
        ch["checks"][0]["violations_sql"] = "SELECT 0"
        ch["checks"][0]["denominator_sql"] = "SELECT 1"

    edit_json(fx, "checks.json", blank)
    rc, out = run(fx)
    assert rc == 1 and "references no live table column" in out, out


def test_duplicate_check_id_exits_1(fx):
    def dup(ch):
        ch["checks"].append(copy.deepcopy(ch["checks"][0]))

    edit_json(fx, "checks.json", dup)
    rc, out = run(fx)
    assert rc == 1 and "duplicate id" in out, out


def test_schema_drift_exits_1(fx):
    edit_json(fx, "inventory.json", lambda i: i.update(schema_drift_vs_declared=["x: drift"]))
    rc, out = run(fx)
    assert rc == 1 and "x: drift" in out, out


def test_failed_reconciliation_exits_1(fx):
    edit_json(fx, "inventory.json", lambda i: i["ledger_reconciliation"].update(ok=False))
    rc, out = run(fx)
    assert rc == 1, out


def test_missing_input_exits_2(fx):
    (fx / "checks.json").unlink()
    rc, out = run(fx)
    assert rc == 2 and "INPUT ERROR" in out, out
