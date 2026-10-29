import shutil, subprocess, sys
from pathlib import Path
root = Path(sys.argv[1]); out = []
M = [
 ("M1 MIN_SHARED 2->1", "src/fpl_agent/db/link_candidates.py", "MIN_SHARED = 2", "MIN_SHARED = 1"),
 ("M2 drop n_p agreement", "src/fpl_agent/db/link_candidates.py", ' & (agg["hits"] >= AGREEMENT * agg["n_p"])', ""),
 ("M3 fpl kickoff cutoff removed", "src/fpl_agent/db/link_candidates.py", '"AND p.kickoff < ?", [season, cutoff]', '"AND p.kickoff < ? OR true", [season, cutoff]'),
 ("M4 snapshot cutoff removed in propose", "src/fpl_agent/db/link_candidates.py", 'teams = ev["teams"][ev["teams"]["snapshot_at"] < pd.Timestamp(cutoff)]', 'teams = ev["teams"]'),
 ("M5 history no reset", "src/fpl_agent/db/link_candidates.py", "                since[pair] = None", "                pass"),
 ("M6 rival check off", "src/fpl_agent/db/link_candidates.py", 'elif len(rec["rivals"]):', 'elif False:'),
 ("M7 reverse uniqueness off", "src/fpl_agent/db/link_candidates.py", 'elif rival_of_q.get(best["q"], 0) > 1:', 'elif False:'),
 ("M8 absent check off", "src/fpl_agent/db/link_candidates.py", 'clean = (agg["contra"] == 0) & (agg["absent"] == 0)', 'clean = (agg["contra"] == 0)'),
 ("M9 has_other conflict off", "src/fpl_agent/db/link_candidates.py", 'elif best["q"] in has_other:', 'elif False:'),
 ("M10 resolve <= -> always", "src/fpl_agent/db/player_links.py", '& (pm["link_valid_from"] <= pd.Timestamp(as_of))', ''),
 ("M11 resolve no clash drop", "src/fpl_agent/db/player_links.py", "    return out[~clash]", "    return out"),
 ("M12 match_frame ignores rollback", "src/fpl_agent/model/features.py", " AND l.rolled_back_at IS NULL", ""),
 ("M13 apply post-insert dup check off", "src/fpl_agent/db/player_links.py", "            if dup:", "            if False:"),
 ("M14 apply held conflict off", "src/fpl_agent/db/player_links.py", "elif q in held_from or p in held_to:", "elif False:"),
 ("M15 audit ignores valid_from", "src/fpl_agent/db/link_audit.py", 'AND (? IS NULL OR valid_from <= ?)", [at, at]', 'AND (? IS NULL OR true OR ? IS NULL)", [at, at]'),
 ("M16 project skips resolve", "src/fpl_agent/model/project.py", 'pm = player_links.resolve(pm[pm["date"] < pd.Timestamp(as_of)], as_of)', 'pm = pm[pm["date"] < pd.Timestamp(as_of)]'),
 ("M17 undo no-op", "src/fpl_agent/db/player_links.py", 'db.con.execute("UPDATE player_links SET rolled_back_at = ? WHERE batch_id = ? "', 'db.con.execute("UPDATE player_links SET rolled_back_at = rolled_back_at WHERE ? IS NOT NULL AND batch_id = ? "'),
 ("M18 stat contradiction off", "src/fpl_agent/db/link_candidates.py", "        same &= _eq(pairs[f\"{k}_f\"], pairs[f\"{k}_q\"])", "        pass"),
]
tests = ["tests/test_link_candidates.py", "tests/test_player_links.py", "tests/test_link_audit.py"]
for name, f, old, new in M:
    p = root / f; src = p.read_text(encoding="utf-8")
    if src.count(old) != 1:
        out.append(f"{name}: SKIP pattern count {src.count(old)}"); continue
    bak = p.with_suffix(".bak"); shutil.copyfile(p, bak)
    try:
        p.write_text(src.replace(old, new), encoding="utf-8")
        r = subprocess.run(["uv", "run", "python", "-m", "pytest", "-q", "--no-cov", "--basetemp=C:/Users/bedor/AppData/Local/Temp/claude/C--Users-bedor-OneDrive-Documents-FPL-fpl-ai-agent-1/c5d997bc-2e0a-4faf-8ba3-4a6534e7c1f6/scratchpad/pt-mut", "-p", "no:cacheprovider", *tests],
                           cwd=root, capture_output=True, text=True)
        last = (r.stdout.strip().splitlines() or ["?"])[-1]
        fails = [l for l in r.stdout.splitlines() if l.startswith("FAILED")]
        out.append(f"{name}: {'KILLED' if r.returncode else 'SURVIVED'} | {fails[:1]} | {last}")
    finally:
        shutil.copyfile(bak, p); bak.unlink()
    assert p.read_text(encoding="utf-8") == src
print("\n".join(out))
