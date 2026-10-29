import shutil, subprocess, sys
from pathlib import Path
root = Path(sys.argv[1]); out = []
M = [
 ("M2 drop n_p agreement", "src/fpl_agent/db/link_candidates.py", ' & (agg["hits"] >= AGREEMENT * agg["n_p"])', ""),
 ("M5 history no reset", "src/fpl_agent/db/link_candidates.py", "                since[pair] = None", "                pass"),
 ("M8 absent check off", "src/fpl_agent/db/link_candidates.py", '(agg["absent"] == 0)', 'True'),
 ("M15 audit ignores valid_from", "src/fpl_agent/db/link_audit.py", "(valid_from <= ? AND recorded_at <= ?)", "(? IS NOT NULL AND recorded_at <= ?)"),
 ("W1 reader ignores recorded_at", "src/fpl_agent/db/player_links.py", "& (recorded <= decided) ", ""),
 ("W2 reader ignores blocked", "src/fpl_agent/db/player_links.py", ' & ~pm["player_id"].isin(blocked)', ""),
 ("W3 project drops blocked", "src/fpl_agent/model/project.py", "as_of, decided, blocked)", "as_of, decided)"),
 ("W4 revalidate keeps links", "src/fpl_agent/db/link_candidates.py", '    ev = {**ev, "links": ev["links"].iloc[0:0]}', "    pass"),
 ("W5 revalidate timed (drops untimed)", "src/fpl_agent/db/link_candidates.py", "ev = load(db, season, at, untimed=True)", "ev = load(db, season, at)"),
 ("W6 reconstructed snapshots allowed", "src/fpl_agent/db/link_candidates.py", "snapshot_at < ? AND {OBSERVED_SNAPSHOT} ", "snapshot_at < ? "),
 ("W7 propose ignores FotMob ingested", "src/fpl_agent/db/link_candidates.py", ' & (ev["fm"]["ingested_at"] < at)]', "]"),
 ("W8 conflicts ignore links", "src/fpl_agent/db/quality.py", 'sql += " UNION ALL " + compare(', 'sql += "" if True else compare('),
 ("W10 orphan check off", "src/fpl_agent/db/quality.py", "t.rolled_back_at IS NULL AND p.player_id IS NULL", "false"),
 ("W11 batch id no suffix", "src/fpl_agent/db/player_links.py", '-{uuid.uuid4().hex[:8]}"', '"'),
 ("W12 coverage ignores unsupported", "src/fpl_agent/db/link_audit.py", "        links.pop(r[\"to_player_id\"], None)", "        pass"),
]
tests = ["tests/test_link_candidates.py", "tests/test_player_links.py", "tests/test_link_audit.py", "tests/test_quality.py"]
for name, f, old, new in M:
    p = root / f; src = p.read_bytes().decode("utf-8")
    if src.count(old) != 1:
        out.append(f"{name}: SKIP pattern count {src.count(old)}"); continue
    bak = p.with_suffix(".bak"); shutil.copyfile(p, bak)
    try:
        p.write_bytes(src.replace(old, new).encode("utf-8"))
        r = subprocess.run(["uv", "run", "python", "-m", "pytest", "-q", "--no-cov", "--basetemp=C:/Users/bedor/AppData/Local/Temp/claude/C--Users-bedor-OneDrive-Documents-FPL-fpl-ai-agent-1/c5d997bc-2e0a-4faf-8ba3-4a6534e7c1f6/scratchpad/pt-rrm", "-p", "no:cacheprovider", *tests],
                           cwd=root, capture_output=True, text=True)
        last = (r.stdout.strip().splitlines() or ["?"])[-1]
        fails = [l for l in r.stdout.splitlines() if l.startswith("FAILED")]
        out.append(f"{name}: {'KILLED' if r.returncode else 'SURVIVED'} | {fails[:1]} | {last}")
    finally:
        shutil.copyfile(bak, p); bak.unlink()
    assert p.read_bytes().decode("utf-8") == src
print("\n".join(out))
