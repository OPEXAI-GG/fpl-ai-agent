import duckdb
p='artifacts/model-deadline-20261027T1634Z/work-data.duckdb'; c=duckdb.connect(p,read_only=True)
for q in ["describe players","select count(*) from fpl_players_gw where season=2026","select count(distinct player_id) from fpl_points_gw where season=2026"]:
 try: print(q,c.execute(q).fetchall()[:12])
 except Exception as e: print(e)
c.close()
