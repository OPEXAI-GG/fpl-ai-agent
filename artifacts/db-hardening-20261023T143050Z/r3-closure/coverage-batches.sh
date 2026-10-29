#!/usr/bin/env bash
# Full suite with coverage, run as one pytest process per batch of test files (appending to one
# data file) so memory is released between batches; threshold applied to the combined data.
set -u -o pipefail
cd "$(dirname "$0")/../../.."
E=artifacts/db-hardening-20261023T143050Z/r3-closure
export COVERAGE_FILE=$E/.coverage
rm -f "$COVERAGE_FILE"
files=(tests/test_*.py)
n=${#files[@]}; size=8; status=0; b=0
for ((i=0; i<n; i+=size)); do
  b=$((b+1))
  batch=("${files[@]:i:size}")
  echo "== batch $b: ${batch[*]}"
  .venv/Scripts/python.exe -m pytest "${batch[@]}" -q -p no:cacheprovider --cov --cov-append \
    --cov-report= --basetemp=$E/pt-cov-$b
  rc=$?; echo "== batch $b exit $rc"; [ $rc -ne 0 ] && status=$rc
done
.venv/Scripts/python.exe -m coverage report --fail-under=85 > $E/coverage-report.txt
rc=$?; tail -3 $E/coverage-report.txt; echo "== coverage report exit $rc"; [ $rc -ne 0 ] && status=$rc
echo "== overall exit $status"; exit $status
