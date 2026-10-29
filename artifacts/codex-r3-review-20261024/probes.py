from pathlib import Path
import sys
import json
from datetime import datetime, timedelta

out = Path(__file__).resolve().parent
sys.path[:0] = [str(out / 'repo/src'), str(out / 'repo')]
from fpl_agent.db.database import FootballDB
from fpl_agent.db import eligibility, source_health
from tests.test_source_health import points, snapshot, attempt, AT

results = {}
db = FootballDB()
cutoff = datetime(2026, 8, 7, 12)
points(db, 1, cutoff + timedelta(minutes=90), fpl_ids=(1, 2))
db.con.execute('INSERT INTO fpl_deadlines VALUES (2026, 1, ?, ?)',
               [cutoff, cutoff - timedelta(days=2)])
for player in (3, 4):
    snapshot(db, 1, cutoff - timedelta(hours=1), fpl_id=player)
results['eligibility_wrong_players'] = eligibility.gameweek(db, 2026, 1)
results['actual_scoring_players_covered'] = db.con.execute(
    'SELECT count(DISTINCT p.fpl_id) FROM fpl_points_gw p JOIN fpl_players_gw s '
    'ON p.season=s.season AND p.gw=s.gw AND p.fpl_id=s.fpl_id').fetchone()[0]
db.close()
db = FootballDB()
attempt(db, 'fpl:bootstrap', False, None, 1, critical=True)
attempt(db, 'fpl:bootstrap', True, 600, -1, critical=True)
results['health_at_before_recovery'] = source_health.sources(db, AT)[0]
db.close()
(out / 'probes.json').write_text(json.dumps(results, indent=2))
print(json.dumps(results, indent=2))
