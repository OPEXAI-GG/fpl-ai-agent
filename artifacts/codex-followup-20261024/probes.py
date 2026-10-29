from pathlib import Path
from datetime import datetime
import json
import sys

out = Path(__file__).resolve().parent
sys.path.insert(0, str(out / 'repo/src'))
import pandas as pd
from fpl_agent.db.database import FootballDB, verify_export, ExportError
from fpl_agent.model.features import player_gw_frame, positions_as_of

results = {}
for label, later_gw in [('original_other_gw', 3), ('late_own_gw', 1)]:
    db = FootballDB()
    db.upsert('fpl_points_gw', pd.DataFrame([dict(season=2026, gw=1, fpl_id=1,
        player_id=1, fixture_id=1, minutes=90)]))
    def snap(gw, at, pos):
        db.upsert('fpl_players_gw', pd.DataFrame([dict(season=2026, gw=gw, fpl_id=1,
            player_id=1, snapshot_at=at, position=pos)]))
    snap(2, datetime(2026, 8, 2), 'MID')
    cutoff = datetime(2026, 8, 3)
    before = positions_as_of(player_gw_frame(db), db, cutoff)['position'].iloc[0]
    snap(later_gw, datetime(2026, 8, 4), 'DEF')
    after = positions_as_of(player_gw_frame(db), db, cutoff)['position'].iloc[0]
    results[label] = dict(before=before, after=None if pd.isna(after) else after,
                         cutoff_invariant=bool(before == after))
    db.close()
src = FootballDB()
src.con.execute("INSERT INTO teams VALUES (1, 'example', 'example')")
export = out / 'probe-export'
assert not export.exists()
src.export_parquet(export)
m = json.loads((export / 'manifest.json').read_text())
del m['tables']['teams']
(export / 'manifest.json').write_text(json.dumps(m))
problems = verify_export(export)
target = FootballDB()
try:
    target.import_parquet(export, require_manifest=True)
    refused = False
except ExportError:
    refused = True
results['manifest'] = dict(problems=problems, refused=refused,
    imported_rows=target.con.execute('SELECT count(*) FROM teams').fetchone()[0])
src.close()
target.close()
(out / 'probes.json').write_text(json.dumps(results, indent=2))
print(json.dumps(results, indent=2))
