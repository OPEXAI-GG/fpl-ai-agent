import json, sys, datetime
p = "hive/tasks.json"
raw = open(p, "rb").read()
assert not raw.startswith(b"\xef\xbb\xbf")
t = json.loads(raw.decode("utf-8"))
items = t if isinstance(t, list) else t["tasks"]
now = datetime.datetime.now(datetime.UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")
tid, status, result = sys.argv[1], sys.argv[2], (sys.argv[3] if len(sys.argv) > 3 else None)
for x in items:
    if x.get("id") == tid:
        x["status"] = status; x["updatedAt"] = now
        if status == "doing": x.setdefault("startedAt", now)
        if status == "done": x["doneAt"] = now
        if result: x["result"] = result
        if len(sys.argv) > 4: x["progress"] = sys.argv[4] + " (" + now + ")"
open(p, "w", encoding="utf-8", newline="\n").write(json.dumps(t, indent=2, ensure_ascii=False) + "\n")
