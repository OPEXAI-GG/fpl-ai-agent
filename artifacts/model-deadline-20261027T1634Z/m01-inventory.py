from pathlib import Path
import duckdb,hashlib,json
root=Path('artifacts/model-deadline-20261027T1634Z')
src=Path('artifacts/db-links-20261026/work.duckdb')
copy=root/'work-data.duckdb'
c=duckdb.connect(str(copy),read_only=True)
queries={
'fpl_points_ingest':"select min(ingested_at),max(ingested_at),count(*),count(try_cast(json_extract_string(stats,'$.expected_goals') as double)),count(try_cast(json_extract_string(stats,'$.expected_assists') as double)) from fpl_points_gw where season=2026",
'fpl_snapshots':"select gw,count(*),count(distinct snapshot_at),min(snapshot_at),max(snapshot_at),count(*) filter(where json_extract(raw,'$.element_type') is not null) from fpl_players_gw where season=2026 group by gw order by gw",
'fpl_deadlines':"select count(*),min(deadline_time),max(deadline_time),min(observed_at),max(observed_at) from fpl_deadlines where season=2026",
'field_ownership':"select count(*),count(distinct gw),count(distinct fpl_id),min(fetched_at),max(fetched_at) from field_ownership where season=2026",
'match_stats':"select m.competition_id,s.source,s.stat,count(*),count(s.value),max(s.ingested_at) from player_match_stats s join matches m using(match_id) where m.season=2026 group by 1,2,3 order by 1,2,3",
'match_odds_log':"select count(*) from match_odds_log",
'match_odds':"select source,count(*),count(distinct match_id),max(ingested_at) from match_odds group by source order by source",
'team_match':"select competition_id,source,count(distinct tm.match_id),count(*),max(m.match_date),avg(tm.xg_for) from team_match tm join matches m using(match_id) where m.season=2026 group by 1,2 order by 1,2",
'player_links':"select source,count(*),count(*) filter(where recorded_at is not null),min(recorded_at),max(recorded_at),min(valid_from),max(valid_from) from player_links where rolled_back_at is null group by source"
}
result={'source_db':str(src),'source_sha256':hashlib.sha256(src.read_bytes()).hexdigest(),'copy_sha256':hashlib.sha256(copy.read_bytes()).hexdigest(),'original_dev_db_sha256':hashlib.sha256(Path('data_cache/football.duckdb').read_bytes()).hexdigest(),'queries':{}}
for name,sql in queries.items():
 try:result['queries'][name]=c.execute(sql).fetchall()
 except Exception as e:result['queries'][name]=str(e)
c.close()
(root/'m01-source-inventory.json').write_text(json.dumps(result,indent=2,default=str),encoding='utf-8')
print(json.dumps(result,indent=2,default=str))
