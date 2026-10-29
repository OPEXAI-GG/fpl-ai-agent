import duckdb,json
p='artifacts/model-deadline-20261027T1634Z/work-data.duckdb'
c=duckdb.connect(p,read_only=True)
for q in [
 "select min(ingested_at),max(ingested_at),count(*),count(expected_goals),count(expected_assists) from fpl_points_gw where season=2026",
 "select gw,count(*),min(snapshot_at),max(snapshot_at),count(*) filter(where snapshot_kind='observed'),count(*) filter(where snapshot_kind='reconstructed') from fpl_players_gw where season=2026 group by gw order by gw",
 "select count(*),min(deadline_time),max(deadline_time) from fpl_deadlines where season=2026",
 "select batch_id,count(*),min(recorded_at),max(recorded_at) from player_links where rolled_back_at is null group by batch_id order by batch_id",
 "select count(*) from matches where season=2026 and competition_id='ENG1'"
]:
 try: print(json.dumps({'query':q,'rows':c.execute(q).fetchall()},default=str))
 except Exception as e: print(json.dumps({'query':q,'error':str(e)}))
c.close()
