import sys
from pathlib import Path
from datetime import datetime
from fpl_agent.db.database import FootballDB
from fpl_agent.model.features import player_gw_frame, minutes_features, MINUTES_FEATURES
from fpl_agent.model.project import history_as_of
from fpl_agent.model.minutes import training_rows
p='artifacts/model-deadline-20261027T1634Z/work-data.duckdb'
db=FootballDB(p,schema='read')
try:
 f=player_gw_frame(db,min_season=2026)
 print('rows',len(f),'weeks',f.groupby('gw').agg(rows=('player_id','size'),kickoff_min=('kickoff','min'),kickoff_max=('kickoff','max')).to_dict('index'))
 for gw in range(1,9):
  k=db.con.execute('select min(kickoff) from fpl_points_gw where season=2026 and gw=? and fixture_id<>0',[gw]).fetchone()[0]
  h=history_as_of(f,db,2026,gw,datetime.combine(k.date(),datetime.min.time()) if k else datetime.now())
  tr=training_rows(h) if len(h) else h
  print(gw,k,'hist',len(h),'train',len(tr),'non_null_cols',[c for c in MINUTES_FEATURES if len(tr) and tr[c].notna().any()])
finally: db.close()


