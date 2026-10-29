# Gate 0 G3 preflight: 23-table exporter and legacy archive on patch r3 (FAA-40)

> **Scope (god, 2026-10-29):** this is a preliminary exporter/legacy-archive preflight only. **G3 is NOT complete.** It still needs the full 23-to-34 migration and rollback rehearsal on the pinned archive, after Nabil's 34-table provenance patch is independently approved.

Zaza (`zaza-mvsc3unw`), 2026-10-29 07:28–07:48 UTC. Scratch only. No production or GitHub writes, no
credentials used beyond one read-only broker GET, no commit, push, release, publish, migration or install.
The original archive, the FAA-7 evidence and `data_cache/` were not touched.

## Verdict
**Every preflight scenario passed on the exact patch, under the stand-ins listed below.** This does not close G3 (see the scope note). Two items are still
open: the real 15-case Linux/zstd shell suite (blocked, see §5), and P7 against the real `gh` binary.

## 1. Inputs (verified first)
| Item | Result |
| --- | --- |
| Patch `gate0-release-51f0def.patch` | SHA-256 `73751d56…f6a1`, matches |
| Pinned archive r20261028 | `artifacts/FAA-7-production/r20261028/football-db-20261028.tar.zst` and the Downloads original both have SHA-256 `3dc27152…c69f` and are 195,782,284 B. Only copies were used. |
| Patched tree | Local clone of master, checked out at `51f0defa3642…` with `autocrlf=false`. `git apply --index` applies cleanly: 6 files, +903/−35. The 6 files are byte-identical to `release-51f0def/tree/`, and all 4 scripts are **100755** in the index (`ev/modes.txt`, `ev/tree-compare.txt`). `git archive` did not work as a base, because the global autocrlf setting adds CRLF and the patch then fails. Use a clone or checkout. |

## 2. Environment and stand-ins (read this before quoting results)
- Real Linux: the existing WSL2 **Ubuntu 24.04** (kernel 6.6.87), real `bash`, **GNU tar 1.35** and coreutils,
  with a clean Linux PATH (`tools/env.sh`). The scripts' own guard (Linux plus a `zstd` on PATH) passed.
- **`zstd` is a stand-in** (`tools/zstd`). WSL has no zstd binary. The stand-in is a stdin-to-stdout filter
  that runs Windows Python 3.15 `compression.zstd`, file to file. Streaming across the WSL/Windows boundary
  deadlocked, so that approach was dropped. Its call log (`ev/tool-calls.log`) shows GNU tar calling it as
  `zstd` to compress and `zstd -d` to decompress, as real zstd would be called.
- **`VERIFY` is a stand-in** (`tools/verify`). It runs the *patched* `fpl-agent verify-export` (r3 `cli.main`)
  in the repo's Windows `.venv`. WSL has no uv or duckdb. `uv run --no-sync` itself was not exercised.
- **`gh` is a stand-in.** It is Pablo's `FAKE_GH`, extracted word for word from the patched
  `tests/test_export_release.py`, with a file's mtime as its `updatedAt`. The stand-in release lives in
  `scratchpad/g3/store`. It was seeded with two copies of the pinned bytes: the rolling asset, updated at
  production's 2026-10-28T16:37:13Z, and a **stand-in** dated copy `football-db-20261028T163713Z`.
  Production has no dated copy; that one exists only so a rollback to the old archive could be shown.
- Data work (import and export) ran in the Windows `.venv` (duckdb 1.5.6, Python 3.13) against the patched
  `src/`. Restore's own temporary folder is WSL `/tmp`, and its exit trap removes it.

## 3. Results
| # | Scenario | Result |
| --- | --- | --- |
| P5 | `check_archive.sh` on the pinned legacy archive | **exit 0.** All 22 members passed: `./` plus 21 `./<table>.parquet`, owner runner/runner (`ev/legacy-listing.txt`). |
| A | Restore of the legacy archive (patched restore, no `DATA_ASSET`) | exit 0, "legacy export without a manifest: accepted". The equal `updatedAt` tie kept the rolling asset. 21 tables restored, byte-identical to the FAA-7 extraction (`ev/legacy-restored.sha256` = `ev/faa7-export.sha256`). |
| B1 | Legacy import with `require_manifest=True` | refused: `ExportError: legacy: no manifest.json` |
| B2 | Legacy import, default | 21 tables loaded into a new scratch DB |
| B3 | Export | **23 parquet files + `manifest.json`** (keys created_at, tables, version). `league_translation` and `transfers` are exported empty. Rows equal FAA-7's `materialize-rows.json` for every table. `verify_export` = [] and `verify-export --complete` exit 0. |
| C1 | Publish (new) | exit 0. Dated copy `football-db-20261029T074253Z` uploaded first, then the rolling asset replaced, 195,509,026 B each. Published SHA-256 `1aeb5de5…29b8`. |
| C2 | Restore (new) | exit 0. The manifest was verified and 23 tables restored, **byte-identical** to the export. |
| C3 | Rollback to the old archive (`DATA_ASSET=football-db-20261028T163713Z…`) | exit 0, legacy accepted, identical to the legacy restore |
| C4 | Rollback to dated copy 1 | exit 0, identical to the export |
| P4 | `keeprolling`: the rolling replacement fails (HTTP 502), the old rolling asset stays | publish **exit 1** after the dated copy 2 was confirmed. Restore with no `DATA_ASSET` printed "football-db.tar.zst (updated 07:43:16Z) is older than the newest dated copy … restoring the dated copy". exit 0, identical. |
| P6 | Newest dated copy listed but damaged (truncated to 4 KiB, newest `updatedAt`) | restore **fails closed** with exit 2 from tar (unexpected end of stream). The target folder was not created. **No recovery hint was printed**, which confirms Wegz's P6. The documented recovery `DATA_ASSET=football-db.tar.zst` then restored correctly (identical). |
| C7 | Rolling asset listed but damaged and newest (`badrolling`) | fails closed with exit 2 and the target is not created. The last line is the hint `…rerun with DATA_ASSET=football-db-20261029T074407Z.tar.zst`. Rerunning with that hint restored correctly (identical). |
| C8 | Archive with a stray `notes.txt` | `check_archive.sh` exit 1: "archive member not allowed: -rwxrwxrwx ./notes.txt" |
| C9 | Publish of an export missing `players.parquet` | exit 1 at the first verify: "players: file missing". **Nothing uploaded** (asset count unchanged). |
| CR | Code rollback: unpatched master `51f0def` `import_parquet` on the new archive (manifest plus 2 empty tables) | It loads: 28,061,044 rows, 21 tables with rows, the same totals as the export. Master's restore is `tar --zstd -xf` with no member filter, so the extra `manifest.json` is harmless. Reverting the code does not strand new archives. |

Full logs: `ev/stepA.log`, `ev/stepB.json`, `ev/stepC.log`, `ev/p6.stderr`, `ev/c7.stderr`.

## 4. P7: `updatedAt` from the real release (read-only)
- **The real `gh` CLI is not installed** on Windows or in WSL, so `gh release view data --json assets`
  could not be run. **Not verified.**
- Read-only `GET repos/OPEXAI-GG/fpl-ai-agent/releases/tags/data` through the broker
  (`ev/p7-release-data.json`): the release does carry a per-asset `updated_at`. This is a fact for the
  REST API only. That gh exposes it as `updatedAt` under `--json assets` is an **inference**.
- **New fact:** the live asset was replaced again: `football-db.tar.zst` id **687686806**, 196,104,758 B,
  created 07:08:59Z and updated **2026-10-29T07:09:08Z**. It is the only asset; there are still no dated
  copies in production. r20261028 (`3dc27152…`) is therefore no longer the live asset. It remains the
  preserved pinned input for this rehearsal. I did not download the new asset.
- Remaining check: one `gh release view data --json assets --jq '.assets[] | "\(.name) \(.updatedAt)"'`
  on the CI runner. A value other than null proves P4 is active; null means it degrades silently to r2.

## 5. Blocker: the real Linux/zstd shell suite
**Not run.** WSL Ubuntu 24.04 exists, but it lacks **`zstd`, `uv`, `duckdb` and `pytest`**
(`command not found` / `ModuleNotFoundError`). Installing them is out of scope. The 15 shell cases in
`tests/test_export_release.py` therefore remain unexecuted. This rehearsal runs the same scripts and the
same fake gh by hand, but it is **not** a substitute for green CI with real zstd.

## 6. Findings for Pablo, Wegz and god
1. **P6 confirmed.** A damaged newest dated copy blocks restore and prints no hint. It is safe, because
   nothing is written. Add `DATA_ASSET=football-db.tar.zst` to SCOPE §6 recovery, or print the hint when
   `asset == newest`.
2. **P5 closed** for r20261028 under GNU tar 1.35.
3. **P7 still open** until the real `gh` output is seen. The REST API has the field.
4. The patch base must be a checkout, not `git archive`, on this machine (autocrlf). Wegz's apply check and
   the commit should keep using `git apply --index` on a checkout.
5. Production's rolling asset changed at 07:09Z today. A pinned identity for G4 or the first verified export
   needs a fresh read-only metadata fetch.

## Reproduce
Scripts are in `tools/`: `env.sh` (paths), `stepA.sh` and `stepC.sh` (run inside WSL:
`wsl -d Ubuntu-24.04 -- bash <G3>/tools/stepC.sh`), and `stepB.py` (Windows `.venv`, args `<G3> <repo>`).
G3 = `%LOCALAPPDATA%\Temp\claude\…\scratchpad\g3`, 4.4 GB, not in the repo.
