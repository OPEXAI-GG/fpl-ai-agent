"""sha256 of every file under src/, tests/, rules/ (no __pycache__) plus one combined fingerprint."""
import hashlib, json, pathlib, subprocess, sys
files = sorted(p for d in ("src", "tests", "rules") for p in pathlib.Path(d).rglob("*")
               if p.is_file() and "__pycache__" not in p.parts)
h = hashlib.sha256(); per = {}
for f in files:
    d = hashlib.sha256(f.read_bytes()).hexdigest(); per[f.as_posix()] = d
    h.update(f"{f.as_posix()}:{d}\n".encode())
head = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
out = {"head": head, "files": len(files), "fingerprint": h.hexdigest(), "per_file": per}
pathlib.Path(sys.argv[1]).write_text(json.dumps(out, indent=1))
print(out["fingerprint"], out["files"])
