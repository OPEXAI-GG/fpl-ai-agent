"""T01: fetch public FPL bootstrap/fixtures into isolated storage and record the event deadline."""
import json, sys, urllib.request
from datetime import datetime, timezone, timedelta
from pathlib import Path

out = Path(sys.argv[1])
UA = {"User-Agent": "fpl-agent-dry/1.0"}
rec = {}
for name, url in [("bootstrap", "https://fantasy.premierleague.com/api/bootstrap-static/"),
                  ("fixtures", "https://fantasy.premierleague.com/api/fixtures/")]:
    at = datetime.now(timezone.utc)
    body = urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=60).read()
    (out / f"{name}.json").write_bytes(body)
    rec[name] = {"url": url, "fetched_at_utc": at.isoformat(timespec="seconds"), "bytes": len(body)}
boot = json.loads((out / "bootstrap.json").read_bytes())
dubai = timezone(timedelta(hours=4))
for e in boot["events"]:
    if e["is_next"] or e["is_current"]:
        d = datetime.fromisoformat(e["deadline_time"].replace("Z", "+00:00"))
        rec.setdefault("events", []).append({
            "id": e["id"], "is_current": e["is_current"], "is_next": e["is_next"], "finished": e["finished"],
            "deadline_time": e["deadline_time"], "deadline_dubai": d.astimezone(dubai).isoformat()})
rec["n_elements"] = len(boot["elements"])
(out / "fetch_record.json").write_text(json.dumps(rec, indent=2))
print(json.dumps(rec, indent=2))
