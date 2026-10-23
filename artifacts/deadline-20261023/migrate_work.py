"""T01/T02: apply the R1 relabel to the deadline WORKING COPY only (never data_cache/)."""
import json, sys
from pathlib import Path
from fpl_agent.db import fpl_identity as fi
from fpl_agent.db.database import FootballDB

path = Path(sys.argv[1]).resolve()
assert "data_cache" not in path.parts, path
db = FootballDB(str(path))
plan = fi.preflight(db.con)
out = {"preflight_problems": plan.problems, "counts": plan.counts, "migrate": fi.migrate(db.con),
       "noop": fi.migrate(db.con), "seed": fi.FplIdentity(db, 2026).seed_codes(),
       "labels": db.con.execute("SELECT source, count(*) FROM player_ids GROUP BY 1 ORDER BY 1").fetchall()}
db.close()
print(json.dumps(out, indent=1, default=str))
