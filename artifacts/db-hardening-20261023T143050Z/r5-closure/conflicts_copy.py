"""Read-only: F-16 conflicts and exclusions on the prod snapshot copy, with the season-bound join."""
import json, sys
from fpl_agent.db.database import FootballDB
from fpl_agent.db import eligibility
from fpl_agent.db.quality import source_conflicts
db = FootballDB(sys.argv[1], schema="read")
c = source_conflicts(db)
mids, fx = eligibility.excluded_fixtures(db)
out = {"conflicts": len(c), "matches": sorted(mids), "excluded_fixtures": sorted(fx),
       "by_season": sorted({(x["season"], x["match_id"]) for x in c})}
db.close()
print(json.dumps(out, indent=1))
