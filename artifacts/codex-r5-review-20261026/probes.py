from pathlib import Path
import sys
import json
from datetime import datetime, timedelta
import pandas as pd

out = Path(__file__).resolve().parent
sys.path[:0] = [str(out / 'repo/src'), str(out / 'repo')]
from fpl_agent.db.database import FootballDB
from fpl_agent.db import eligibility
from fpl_agent.db.quality import source_conflicts
from fpl_agent.model import minutes, backtest, replay
from tests.synth import build_world

results = {}
db = FootballDB()
for season, mid, goals in [(2025, 40, 0), (2026, 50, 1)]:
    db.upsert('fpl_points_gw', pd.DataFrame([dict(season=season, gw=1, fpl_id=1,
        player_id=1, fixture_id=11, minutes=90, stats=json.dumps({'goals_scored': goals}))]))
    db.con.execute('INSERT INTO matches (match_id, season) VALUES (?, ?)', [mid, season])
    db.con.execute('INSERT INTO match_ids VALUES (?, ?, ?)', [f'fpl-{season}', '11', mid])
    db.con.execute("INSERT INTO player_match_stats VALUES (?, 1, 'fotmob', 'goals', ?, NULL)",
                   [mid, goals])
results['matching_seasons_false_conflicts'] = source_conflicts(db)
results['matching_seasons_false_exclusions'] = sorted(eligibility.excluded_fixtures(db)[1])
db.close()

db = build_world(n_teams=6, seasons=(2023, 2024), seed=7).db
gw = 10
ko = db.con.execute('SELECT min(kickoff) FROM fpl_points_gw WHERE season=2024 AND gw=10').fetchone()[0]
cut = ko - timedelta(minutes=90)
db.con.execute('INSERT INTO fpl_deadlines VALUES (2024, 10, ?, ?)', [cut, cut-timedelta(days=2)])
db.con.execute('UPDATE fpl_players_gw SET raw=?, snapshot_at=? WHERE season=2024 AND gw=10',
               [json.dumps({'element_type': 2}), cut-timedelta(hours=3)])
assert eligibility.gameweek(db, 2024, 10)['class'] == 'eligible'
class Captured(Exception):
    pass
seen = []
original = minutes.MinutesModel.__dict__['fit']
def capture(cls, history):
    seen.append(history.copy())
    raise Captured()
minutes.MinutesModel.fit = classmethod(capture)
def run_both():
    for fn in (backtest.projection_backtest, replay.replay_season):
        try:
            fn(db, 2024, [10], validated=True)
        except Captured:
            pass
run_both()
snap = db.query('SELECT * FROM fpl_players_gw WHERE season=2024 AND gw=4')
db.upsert('fpl_players_gw', snap.assign(snapshot_at=cut+timedelta(hours=2),
    position=snap.position.map({'GKP':'FWD','FWD':'GKP','DEF':'MID','MID':'DEF'}),
    now_cost=snap.now_cost+9))
run_both()
for label, i in [('backtest', 0), ('replay', 1)]:
    a,b=seen[i],seen[i+2]
    results[label+'_fit_history'] = dict(rows=len(a), unchanged=a.equals(b),
        changed_positions=int((a.position.fillna('NULL').to_numpy()!=b.position.fillna('NULL').to_numpy()).sum()))
minutes.MinutesModel.fit = original
a = replay.pool_as_of(db, 2024, 10, cut).set_index('player_id').sort_index()
snap = db.query('SELECT * FROM fpl_players_gw WHERE season=2024 AND gw=10')
db.upsert('fpl_players_gw', snap.assign(snapshot_at=cut+timedelta(hours=3), now_cost=snap.now_cost+9))
b = replay.pool_as_of(db, 2024, 10, cut).set_index('player_id').sort_index()
results['replay_pool_late_prices'] = dict(changed_players=int((a.price!=b.price).sum()),
    gameweek_still_eligible=eligibility.gameweek(db,2024,10)['class'])
db.close()
(out / 'probes.json').write_text(json.dumps(results,indent=2))
print(json.dumps(results,indent=2))
