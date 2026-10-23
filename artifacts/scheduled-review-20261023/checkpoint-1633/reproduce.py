"""Two independent review probes, synthetic databases only; refuses reused output."""
from pathlib import Path
from datetime import datetime
import json
import sys

out = Path(__file__).resolve().parent
sys.path.insert(0, str(out / 'repo/src'))
import pandas as pd
from fpl_agent.db.database import FootballDB, verify_export
from fpl_agent.model.features import player_gw_frame, positions_as_of

db = FootballDB()
db.upsert('fpl_points_gw', pd.DataFrame([dict(season=2026, gw=1, fpl_id=1,
          player_id=1, fixture_id=1, minutes=90)]))
def snap(gw, at, pos):
    db.upsert('fpl_players_gw', pd.DataFrame([dict(season=2026, gw=gw, fpl_id=1,
              player_id=1, snapshot_at=at, position=pos)]))
snap(2, datetime(2026, 8, 2), 'MID')
cutoff = datetime(2026, 8, 3)
before = positions_as_of(player_gw_frame(db), db, cutoff)['position'].iloc[0]
snap(3, datetime(2026, 8, 4), 'DEF')
after = positions_as_of(player_gw_frame(db), db, cutoff)['position'].iloc[0]
results = {'cutoff_invariance': dict(before=before, after=None if pd.isna(after) else after,
                                   pass_=bool(before == after))}
db.close()
src = FootballDB()
src.con.execute("INSERT INTO teams VALUES (1, 'example', 'example')")
export = out / 'reproducer-export'
assert not export.exists()
src.export_parquet(export)
manifest = json.loads((export / 'manifest.json').read_text())
del manifest['tables']['teams']
(export / 'manifest.json').write_text(json.dumps(manifest))
problems = verify_export(export)
target = FootballDB()
target.import_parquet(export, require_manifest=True)
results['unlisted_file'] = dict(verification_problems=problems,
    unlisted_rows_imported=target.con.execute('SELECT count(*) FROM teams').fetchone()[0])
src.close()
target.close()
print(json.dumps(results, indent=2))
with (out / 'reproducer-results.json').open('x') as f:
    json.dump(results, f, indent=2)
