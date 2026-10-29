#!/usr/bin/env bash
# Revert one fix at a time on the working tree, run the new tests, restore. Each must FAIL.
set -u
cd "$(dirname "$0")/../../.."
PY=.venv/Scripts/python
run() { # name file python-replacement-script
  cp "$2" "$2.bak"
  $PY - "$2" <<<"$3"
  $PY -m pytest -q -p no:cacheprovider --basetemp="artifacts/db-hardening-20261023T143050Z/r5-closure/tmp-mut-$1" tests/test_validated_eval.py 2>&1 | tail -1 | sed "s/^/$1: /"
  mv "$2.bak" "$2"
}
run fit-uncut src/fpl_agent/model/backtest.py 'import sys;p=sys.argv[1];s=open(p,encoding="utf-8").read();s=s.replace("history = project.history_as_of(frame, db, season, gw, cutoff)","history = frame[(frame[\"season\"] < season) | ((frame[\"season\"] == season) & (frame[\"gw\"] < gw))]");open(p,"w",encoding="utf-8").write(s)'
run replay-fit-uncut src/fpl_agent/model/replay.py 'import sys;p=sys.argv[1];s=open(p,encoding="utf-8").read();s=s.replace("minutes.MinutesModel.fit(project.history_as_of(frame, db, season, gw, cutoff))","minutes.MinutesModel.fit(frame[(frame[\"season\"] < season) | ((frame[\"season\"] == season) & (frame[\"gw\"] < gw))])");open(p,"w",encoding="utf-8").write(s)'
run pool-uncut src/fpl_agent/model/replay.py 'import sys;p=sys.argv[1];s=open(p,encoding="utf-8").read();s=s.replace("pool = pool_as_of(db, season, gw, cutoff)\n","pool = pool_as_of(db, season, gw).assign(status=\"a\", chance=None)\n");open(p,"w",encoding="utf-8").write(s)'
run odds-unknown src/fpl_agent/model/backtest.py 'import sys;p=sys.argv[1];s=open(p,encoding="utf-8").read();s=s.replace("known_at_odds=validated","known_at_odds=False");open(p,"w",encoding="utf-8").write(s)'
run season-unbound src/fpl_agent/db/quality.py 'import sys;p=sys.argv[1];s=open(p,encoding="utf-8").read();s=s.replace("i.season = x.season AND i.source_id","i.source_id");open(p,"w",encoding="utf-8").write(s)'
run alias-dup src/fpl_agent/db/quality.py 'import sys;p=sys.argv[1];s=open(p,encoding="utf-8").read();s=s.replace("(SELECT DISTINCT m.season","(SELECT m.season");open(p,"w",encoding="utf-8").write(s)'
run excl-unbound src/fpl_agent/db/eligibility.py 'import sys;p=sys.argv[1];s=open(p,encoding="utf-8").read();s=s.replace("i ON i.season = x.season AND","i ON");open(p,"w",encoding="utf-8").write(s)'
git diff --stat -- src | tail -1
