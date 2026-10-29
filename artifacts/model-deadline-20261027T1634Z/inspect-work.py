from fpl_agent.db.database import schema_status
from pathlib import Path
import duckdb,hashlib
p=Path('artifacts/db-links-20261026/work.duckdb')
print('status',schema_status(p))
print('sha',hashlib.sha256(p.read_bytes()).hexdigest(),'bytes',p.stat().st_size)
con=duckdb.connect(str(p),read_only=True)
try:
 for t in ['player_links','player_match_stats','fpl_deadlines','source_attempts','migration_log','repair_log']:
  try: print(t,con.execute(f'select count(*) from "{t}"').fetchone()[0],con.execute(f'describe "{t}"').fetchall())
  except Exception as e: print(t,'absent',e.__class__.__name__)
finally:con.close()
