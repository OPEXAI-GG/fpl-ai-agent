#!/usr/bin/env bash
# One resource-bounded batch of the full suite: batch $1 (1-based) of the test files taken $2
# at a time, appending to one coverage data file. `report` instead runs the threshold check
# directly on the combined data. Every exit code is the command's own (pipefail, no pipes).
set -u -o pipefail
cd "$(dirname "$0")/../../.."
E=artifacts/db-hardening-20261023T143050Z/r4-gate
export COVERAGE_FILE=$E/.coverage
if [ "$1" = report ]; then
  .venv/Scripts/python.exe -m coverage report --fail-under=85 > $E/coverage-report.txt
  rc=$?; echo "== coverage report exit $rc" | tee -a $E/gate.log; exit $rc
fi
files=(tests/test_*.py); size=${2:-4}; start=$(( ($1 - 1) * size ))
batch=("${files[@]:start:size}")
[ ${#batch[@]} -eq 0 ] && { echo "no batch $1"; exit 0; }
echo "== batch $1: ${batch[*]}" >> $E/gate.log
.venv/Scripts/python.exe -m pytest "${batch[@]}" -q -p no:cacheprovider --cov --cov-append \
  --cov-report= --basetemp=$E/pt-$1 > $E/batch-$1.log 2>&1
rc=$?; echo "== batch $1 exit $rc: $(tail -1 $E/batch-$1.log)" | tee -a $E/gate.log; exit $rc
