# npm home-folder incident: triage

Triage by Zaza (`zaza-mvsc3unw`), 2026-10-28 around 20:05 UTC. Everything here comes from read-only
checks. Nothing was installed, removed, cleaned or restored, and no suspect tool was run. Mona's
evidence in `hive/agents/mona-mvsc234j/npm-home-incident/` was left unchanged.

## Summary
A single `npm install axe-core@4.10.2 --no-audit --no-fund` (npm 11.12.1, node 24.20.0) was run on
2026-10-28 at 19:55:37Z from an **empty** Claude scratchpad folder (`…\scratchpad\tools`). npm found
no `package.json` there, walked up the folder tree and used **`C:\Users\bedor`** (the home folder) as
the project root, because that folder had a `node_modules`. It then wrote a new home `package.json`
listing only axe-core, and **deleted 641 installed package folders (533 top-level)** that the new
manifest did not list. The command finished with exit 0 ("info ok"). The deletion is complete and
permanent; npm deletes directly and does not use the Recycle Bin.

## Verified facts
| Fact | Evidence |
| --- | --- |
| The command, versions and exit code are as above | npm-debug.log lines 0–7, 1204–1205; the same log is at `%LOCALAPPDATA%\npm-cache\_logs\2026-10-28T19_55_37_193Z-debug-0.log` |
| cwd = `C:\Users\bedor\AppData\Local\Temp\claude\…\scratchpad\tools` (npm masks the session id as `***`) | log line 1200 |
| That folder exists, is empty, and was created at 19:55:36Z, 1 s before the install | directory listing |
| Affected root: `C:\Users\bedor` | log "mark retired" paths `C:\Users\bedor\node_modules\…` |
| 641 REMOVE entries (533 top-level), including binaries in `.bin` | log; Mona's `removed-all.txt` and `removed-top-level.txt` |
| Home now has `package.json` (54 B, deps: `axe-core ^4.10.2` only), `package-lock.json` (504 B, only axe-core) and `node_modules/.package-lock.json` (425 B), all written at 19:55Z | file listing and contents |
| What remains in `C:\Users\bedor\node_modules`: `axe-core`, `.bin`, the hidden lockfile and 24 **empty** scope folders (`@anthropic-ai`, `@ruvector`, `@xenova`, …) | listing |
| The old hidden lockfile existed but was out of date, and has been overwritten. Its original content is gone. | log line 13 |
| The log rotation removed 1 older npm log during this run, and 2 during the 19:55:22Z `npm exec playwright --version` run from the repo | log line 10; earlier log line 11 |
| **No package manager is still running.** The only node processes are Adobe CC (since 2026-10-28 01:55Z) and two Codex MCP tools from `~/.codex/mcp-tools` (since 16:21Z). Both are unrelated and were not touched. | process list |
| Command resolution: `npm` → `C:\Program Files\nodejs\npm.cmd`, which hands off to the global npm `%APPDATA%\npm\node_modules\npm` 11.12.1. `node` → `C:\Program Files\nodejs\node.exe` (the hive shim `hive\bin\runtime\node.cmd` is second on PATH). `ruflo` → `%APPDATA%\npm\ruflo.cmd`. `claude-flow`, `agentic-flow`, `agentdb` and `axe` are not on PATH. | `Get-Command -All` |
| The global packages folder (`%APPDATA%\npm\node_modules`: @google, @openai, claude-code-templates, docx, npm, ruflo, to, typescript, update, vercel) is a separate root and was not affected | listing |
| `ruflo` 3.5.48 ships its own copies of its optional dependencies (agentic-flow, agentdb, @ruvector/*, @claude-flow/memory, @claude-flow/embeddings), all present inside its tree | path checks |
| No reference to `C:\Users\bedor\node_modules` in `.claude.json`, `.claude/settings.json` or `.codex/config.toml` (counted matches only; contents not printed) | grep counts |
| npm cache `%LOCALAPPDATA%\npm-cache\_cacache` (4.1 GB) holds at least one tarball version for **all 533** removed top-level package names. For 261 of them, more than one version is cached. | cache index scan |

## Reported by the operator (Mona, via god, 2026-10-28 20:05Z)
- Mona ran the 19:55:37Z install from the empty temp `scratchpad\tools`; npm printed "added 1,
  removed 641" in 8 s. The home package files were created at 23:55:45 local time. Before that, at
  19:55:22Z, she ran `npx playwright --version` in the repo. She ran no further package-manager
  commands. She ran four prototype checks before the pause reached her. None of her processes are
  running now. This matches the log and the folder timestamps above.

## Inference (not proven)
- **What originally installed the home packages:** the removed set (agentic-flow, agentdb, ruvector
  and @ruvector/*, @xenova/transformers, onnxruntime, @anthropic-ai/sdk, @modelcontextprotocol,
  @supabase, pg, bcrypt, better-sqlite3 …) matches the dependency tree of the claude-flow / ruflo
  ecosystem. It was probably installed by an earlier claude-flow/ruflo or agentic-flow setup run
  with the home folder as its working directory. **Low–medium confidence.** No surviving log shows
  that install, and there is no earlier home `package.json`.
- **What is affected:** probably only tools that *resolve modules from the home folder*. Node looks
  in parent folders, so anything under `C:\Users\bedor\…` that lacked its own copy of a dependency
  could have been using `~\node_modules`. That includes global packages and `~\.codex\mcp-tools`.
  ruflo looks safe because it has its own copies. Other tools have not been tested, and I did not run
  any of them.

## What is recoverable
| Item | Status |
| --- | --- |
| List of removed package names (641 paths) | yes: Mona's `removed-all.txt` and `removed-top-level.txt`, and the npm log |
| Exact original versions | **no.** The old hidden lockfile was overwritten and the log records names only. Versions can only be reconstructed from cache index timestamps (ambiguous for 261 packages). |
| Original home `package.json` / `package-lock.json` | **none found.** It probably did not exist, which is why npm pruned everything. |
| Package contents | yes, offline from the npm cache (all 533 names have at least one cached tarball) |
| Windows Previous Versions / File History / shadow copies | **unknown.** Checking needs administrator rights or the Explorer UI, so I did not check. |

## Restoration options (for the user to review; none have been run)
1. **Undo the new manifest only (smallest step).** Move `C:\Users\bedor\package.json`,
   `package-lock.json` and `node_modules\` (now just axe-core) into a dated backup folder. This
   stops npm from treating the home folder as a project again, but it does not bring packages back.
   It is enough if nothing needs `~\node_modules`.
2. **Leave the home folder empty and reinstall per tool (recommended).** Find which tool actually
   breaks. Reinstall it the supported way: for ruflo, `npm install -g ruflo` / its own setup command;
   for the others, inside their own folder. The home folder was never meant to be a project, so this
   avoids recreating a hidden dependency.
3. **Rebuild the old home tree from cache (closest to the original).** Write a home `package.json`
   from `removed-top-level.txt`, picking each version as the newest cached tarball before
   2026-10-28 19:55Z, then run `npm install --offline` in `C:\Users\bedor`. The versions would be a
   best guess, the result may still differ from the original, and it puts the risky home-folder
   project back.
4. **Restore from Windows Previous Versions,** if File History or a restore point covers
   `C:\Users\bedor\node_modules`. This needs the user to check, in Explorer → Properties → Previous
   Versions.

**Prevention:** before running `npm install` from a scratch folder, put a `package.json` in it first
(`npm init -y`), or pass `--prefix <that folder>`. Otherwise npm walks up to the home folder.

## Open questions
- Which agent ran the command (to be confirmed by Mona or god); whether any tool is broken now;
  whether Previous Versions exist.
