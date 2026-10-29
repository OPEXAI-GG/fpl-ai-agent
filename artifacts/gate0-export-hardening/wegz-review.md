# Review: Gate 0 export/publish/restore patch (FAA-35)

Reviewer: Wegz (wegz-mvsc3fox), 2026-10-29. Author: Pablo (pablo-mvsc2uke). Not written by me.

## Reviewed content (SHA-256, all re-hashed by me and equal to `after.sha256` and the FAA-35 card)

| File | Before (`before.sha256`) | After (reviewed) |
|---|---|---|
| `src/fpl_agent/cli.py` | `033547dc…f9f6d` | `089ef9a542d76916fa12f66592c0f82f24acfe905fb1d8528059c7693a8b6942` |
| `scripts/data_publish.sh` | `bb0aed4d…ccea` | `917297c55b9610291aeb24c989e97485f170579b0d0e2aa6d49d15f1c13de134` |
| `scripts/data_restore.sh` | `7b70d658…506c` | `5b3f642183e0d039a166631ba6d633624b9bd4f587f598dcdf14f99cf307dd11` |
| `tests/test_persistence.py` | `ca1a1604…cfe3d3f`¹ | `4e7f668633ca39adbcff11d0df746b5c8d025c5a6bf896358e51d06c64aeb044` |
| `diff-vs-HEAD.patch` | | `9dccd66733ffe78f27163f04b72d20e625889daeac4652e991535ba1caf1c752` |

¹ `ca1a1604f041d6ee4d6472dc3e48d67385d3fe6d81dbc45a7292353cebf3cfdc`.

## Verdict: the patch is internally sound for local use. APPROVE Pablo's four-file delta as a local slice, with conditions

This is **not** approval of a master-to-release change, a push or production use. Pablo's delta can't ship on its
own as `cli.py` stands (S1), and the shell behaviour has not run anywhere yet (S2).

## How I separated Pablo's change from earlier uncommitted work

The patch is against HEAD `c6a0c5f`, so it mixes both. Only hashes of the "before" files were kept, so I proved
the split independently:
- `data_publish.sh`, `data_restore.sh` and `test_persistence.py`: the "before" hashes equal the saved copies in
  `artifacts/codex-r5-review-20261026/repo/` (bb0aed4d, 7b70d658, ca1a1604). I diffed those copies against the
  current files.
- `cli.py`: there is no saved copy. I removed Pablo's two `--complete` hunks from the current file in memory. The
  result hashes to exactly `033547dc…` (the "before" hash). So his `cli.py` change is those hunks and nothing else.
- No other file is in `before/after.sha256`. I found no sign that he touched another file, but the hash lists only
  cover these four. I can't prove a negative for the rest of the dirty tree.

**Pablo's delta (new behaviour), confirmed exact:**
1. `cli.py`: `verify-export --complete` (import of `missing_tables`, the check, and the argument).
2. `data_publish.sh`:
   - verify `--complete` first;
   - unpack the built archive and verify it again;
   - `MAX_ARCHIVE_BYTES` bound;
   - dated copy uploaded without `--clobber` and confirmed listed before the rolling asset is replaced.
3. `data_restore.sh`:
   - `DATA_ASSET` name restricted;
   - archive member allowlist checked before unpacking;
   - legacy archive must hold at least one table;
   - staged sibling folder renamed into place.
4. `test_persistence.py`: fake-gh `dropdated` mode plus 9 tests (2 Python, 6 shell functions with 7 cases).

**Earlier uncommitted hunks in the patch (not Pablo's, not reviewed here):**
- both scripts: the Linux/zstd refusal guard;
- `cli.py`:
  - `--repairs` opt-in, which flips the repair default to off (R2 path);
  - the `created_tables` stderr notice;
  - backtest and replay `--validated` with `NotEligible`;
  - the `db-schema`, `source-health` and `db-eligibility` commands;
  - `db-links` with `--apply`/`--undo` (R1 write path, added after the r5 snapshot);
  - `repair-undo` opening with `schema="require"`, and the "already undone" message;
- `test_persistence.py`: the fixture fix at the old L201 and five earlier tests (full restore, interrupted import,
  file swap after verify, schema require, `db-schema` read-only).

## Checks

**Upload ordering (publish).** Holds as far as the script goes.
- Every check (folder verify, archive verify, size) runs before any `gh` write. `set -euo pipefail` stops the run
  on any failure.
- The dated copy has no `--clobber`, so an existing name makes the upload fail. The name must then be listed
  (`grep -qxF`, exact match) before the rolling asset is replaced.
- All publishers (data, plan non-dry, backfill, review) share the `data` concurrency group, so two publishes don't
  race.
- Limit (P1 below): the replacement of the rolling asset is still not atomic.

**Restore path restrictions.** Sound, and they fail closed.
- `DATA_ASSET` is anchored-regex-checked before any `gh` call. A test confirms no call is made.
- Members are parsed from GNU `tar -tvf` as 6 fields. Accepted are:
  - the `./` directory;
  - regular files named `manifest.json` or `[a-z0-9_]+.parquet` at the top level.
- Everything else is refused: symlinks (`l`), hard links (`h`), subfolders, `../`, absolute paths, and odd names
  (a symlink's name includes `-> target`).
- A non-GNU listing (for example bsdtar's 9 fields) would make every name fail the pattern. That refuses the
  archive; it does not wrongly accept it.
- Assembly in a sibling `mktemp` folder plus `mv` is a same-filesystem rename. The trap removes the stage on
  failure. The earlier "non-empty target" refusal is kept.

**Manifest completeness.**
- `--complete` runs `missing_tables` only after `verify_export` finds nothing wrong.
- `verify_export` already rejects:
  - a missing or legacy manifest, so publish refuses a legacy folder;
  - undeclared or malformed entries;
  - unlisted `.parquet` files;
  - a changed checksum, row count or column list.
- So publish needs a manifest that verifies and every declared table. Restore deliberately does not use
  `--complete`, so older exports stay restorable. That's correct for the old/new restore need in G3.

**Tests.** I re-ran only the matching subset: `pytest tests/test_persistence.py -k "verify_export_complete or
fresh_export_lists or publish or restore_"` → **3 passed, 11 skipped** (scratch basetemp). That covers Pablo's 2
Python tests plus one earlier test. All shell tests skipped here (no zstd). I did not repeat his 104/11 run.

## Findings

**S1 (scope, blocks a scoped release; not a defect in the slice).** `cli.py` can't be taken as a file into a
release from master. Its earlier hunks import modules that exist only in the uncommitted tree:
- untracked: `db/eligibility.py`, `source_health.py`, `link_audit.py`, `link_candidates.py`, `player_links.py`;
- modified: `database.py` (`schema_status`, `SchemaError`, `FootballDB(schema=)`, `missing_tables`), and the model
  files `backtest.py` and `replay.py` (`validated=`).

A G1 release must either pick hunks (`--complete` plus whatever `database.py` export, manifest and
`missing_tables` code master lacks), or explicitly include and review those modules. It must also decide on the R1
(`db-links --apply`) and R2 (repairs now opt-in) paths under G2. Note that `--complete` makes publish depend on
`missing_tables` and `verify_export` in the uncommitted `database.py`.

**S2 (evidence gap).** The publish and restore changes have never run. Every shell test was skipped, and both
scripts refuse to run outside Linux. "Internally sound" here comes from reading the code. Linux CI with all 11
shell tests passing is required before any release candidate.

**P1 (should fix before release). The rolling-asset replacement isn't atomic, and nothing is checked after it.**
- `gh release upload --clobber` replaces an asset of the same name. Depending on the `gh` version it may delete the
  old asset before the new upload finishes. I have not confirmed this for the version CI uses.
- If that upload fails, `football-db.tar.zst` may be missing. Every workflow that restores the default asset would
  then fail closed until someone sets `DATA_ASSET` to the dated copy.
- Fix: after the clobber, confirm the rolling asset is listed with the expected size. Document the fallback to the
  newest dated copy in the runbook, or have restore fall back to it automatically.

**P2 (should fix). Publish and restore accept different archives.**
- Publish verifies with `verify_export`, which ignores non-`.parquet` files and folders. Restore refuses them.
- A folder with a stray file or subfolder could therefore be published, and then no restore would accept it.
- `export_parquet` writes a clean staged folder (its `.prev-*` copies are siblings, not inside), so normal runs
  don't hit this. Hand-made folders do.
- Fix: apply the same member allowlist in publish, to the folder or to `tar -t` of the built archive, before any
  upload.

**P3 (minor).**
- The dated-copy check confirms the name, not the size. Compare the listed `size` with the local size.
- `test_publish_refuses_an_export_lacking_a_declared_table` asserts only a non-zero exit. Also assert
  `declared table not in the export` in its output, so it can't pass for another reason.
- `VERIFY` still defaults to `uv run`, which can sync packages (T2 from FAA-29). In CI that is a choice, but pin it
  with `--frozen` or say why not.

**Carried from FAA-33 (unchanged by this slice).**
- K3: with repairs now opt-in, the help text says a needed repair stops the run with exit 3. Whether `data.yml` and
  `plan.yml` treat that as a visible failure, rather than a run that loaded nothing, is still unverified (G4).
- Provenance writers (`fpl_deadlines`, `source_attempts`), the migration rehearsal (G3) and the first pinned export
  (G4) are not in this slice. Gate 0 does not pass on this patch.

## What can be approved locally

- **Approvable as a local slice:** Pablo's four hunks (P1-P3 to be addressed before a release candidate).
- **Not approvable from this review:**
  - the earlier uncommitted hunks listed above;
  - any commit, push, release or production use;
  - a claim that the shell paths work.

---

# Review r2: isolated master patch (FAA-37 / gate0-master-patch-review), 2026-10-29

Reviewed:
- `release-51f0def/gate0-release-51f0def.patch`, SHA-256
  `05d3acd9e35a4e0380a7c815d1641418ec4f9e3e65f5f3f7e46e9c7d08af2cff` (964 lines);
- `SCOPE.md`, SHA-256 `b1cfc06aaa4d8d215386459cdd274244634cde28197b546c5bcf372225d1389d`.

Both were re-hashed by me.

## Verdict: APPROVE this exact patch as the release-candidate input, not as a release

Approval is for going on to Linux CI and the rehearsal. The open gates god listed still apply:
- Linux and zstd CI with all 13 shell tests green;
- `check_archive.sh` committed with mode 100755;
- the migration and rollback rehearsal (G3).

No push, publish or production approval. Gate 0 does not pass on this patch alone (provenance writers, G4).

## How I checked (read-only plus scratch copies; no repo edits, no test reruns)

- **Applies to master.** I ran `git -c core.autocrlf=false archive 51f0def` into my scratchpad, then
  `git apply` the patch. It applies cleanly, and all 6 resulting files are byte-identical to
  `release-51f0def/tree/`. The patch's `index` lines name the master blobs exactly (database `5c08647`, cli
  `ffb4a61`, publish `612e56f`, restore `a2c8f85`).
- **File modes.** I applied the patch to a scratch index with the scripts set to 100755. `data_publish.sh` and
  `data_restore.sh` **keep 100755**; git only warns ("expected 100644"). New `check_archive.sh` lands as 100644.
  Both scripts call it with `bash`, so it still runs, but the commit must set it to 100755 (open gate).
- **Scope matches SCOPE.md §1–2.**
  - `cli.py` adds only `verify-export [--complete]`, dispatched before `Settings.from_env()`.
  - None of the R1/R2, `db-links`, `--repairs`, schema, eligibility or model hunks are present (resolves S1).
  - `database.py` adds the export, manifest, verify and import code. It depends only on things master already
    has: `now()`, `FootballDB.transaction()`, the 5-column `ingest_state`, and `duckdb`.
  - The callers `pipeline._finish` and `cli._export` / `_open_db` already exist and are unchanged. An
    `ExportError` now fails the run before publishing, which is the intended fail-closed path.
- **Earlier findings.**
  - P1: resolved. The dated copy is confirmed by name and size, then the rolling asset is checked after the
    clobber.
  - P2: resolved. Publish runs `check_archive.sh` on the built archive before any upload, and restore runs it
    before unpacking.
  - P3: resolved. Size checks are added, the test asserts `declared table not in the export`, and `VERIFY`
    uses `uv run --no-sync`. Master workflows run `uv sync --frozen` first, so the environment exists.

## Is fail-closed restore safer? Yes, and acceptable

- **Master `51f0def` behaviour (from the code).**
  - `data_restore.sh` runs with `set -uo pipefail`, without `-e`. Any failure of
    `gh release download … 2>/dev/null` (auth, network, rate limit, missing asset) prints "starting empty" and
    the job continues on an empty database.
  - Master `data_publish.sh` then checks only `ls *.parquet`, and its upload uses `--clobber`.
  - So one transient GitHub error could replace the good asset with a near-empty export.
- **Patched behaviour.** It starts empty only in two cases:
  - `release view` fails with "release not found";
  - the release exists but holds no `football-db*` asset.
  Every other failure stops the job: auth, network, download, archive members, a corrupt archive, or a manifest
  mismatch.
- **Both remaining "start empty" cases are harmless.**
  - In the first, a later publish would have to create the release. If the release really exists, create fails
    and publish stops.
  - In the second, there's no prior export to overwrite. The dated copies are never deleted.
- **Cost.** During a GitHub outage, data and plan runs fail visibly instead of running on empty data. That
  means a missed run, not a corrupted asset. Accept it, and make sure workflow failure alerts are watched.

## Findings (none blocking)

- **P4 (recommend; runbook at minimum). The fallback only covers a missing rolling asset.**
  - If an interrupted `--clobber` leaves a rolling asset that is listed but corrupt, restore picks it and fails
    closed. Recovery is manual (`DATA_ASSET=<newest dated>`). That's safe.
  - If the clobber fails and leaves the **old** rolling asset in place, publish exits 1, which is visible. But
    the next run restores the older rolling asset and silently drops that run's newer dated copy. That can lose
    prospective data such as deadline observations from that run.
  - Fix: write the manual recovery into the runbook, or have restore prefer the newest dated copy when it is
    newer than the rolling asset (`updated_at`).
  - The test `badrolling` exercises only the absent case: it deletes the truncated file before restoring.
- **P5 (minor).**
  - `check_archive.sh` depends on GNU tar's 6-field verbose listing. Any other format is refused, never accepted.
    `ubuntu-latest` uses GNU tar.
  - Whether the pinned `r20261028` legacy archive passes the allowlist is still unproven. G3 must show it.
- **Not re-verified.** Pablo's reported results (50 passed and 13 skipped on the patched copy; the exec-bit test
  failing identically on unpatched master) were not re-run, per instruction.

---

# Review r3: revision 3 of the isolated master patch (gate0-export-revision3-review), 2026-10-29

Reviewed:
- `release-51f0def/gate0-release-51f0def.patch`, SHA-256
  `73751d565414585403d958c5b4d798a926bdbd42f1c7a4cbde3cd9b188c2f6a1`;
- `SCOPE.md`, SHA-256 `12989df99144a46dd1d0e283e8d88220b5f1ca43896fcccb74591173fcaf49b4` (revision 3).

Both were re-hashed by me.

## Verdict: APPROVE revision 3 as the release-candidate input (not a release)

It closes both FAA-37 items, the executable mode and the stale rolling asset, and keeps the isolated scope. No
push, CI dispatch, publish or production approval.

## Checks (scratch copies only; no repo edits, no test runs)

- **Applies to master.** I extracted `51f0def` with `git archive` and applied the patch with
  `git apply --index`. It applies cleanly, and the 6 changed files are byte-identical to `release-51f0def/tree/`.
- **Scope unchanged since revision 2.** I compared with my saved revision-2 tree. `cli.py`, `database.py`,
  `data_publish.sh` and `check_archive.sh` are byte-identical. Only `data_restore.sh` and
  `tests/test_export_release.py` changed, both for P4. No new files and no other hunks.
- **Mode (closed).**
  - The patch now declares `new file mode 100755` for `check_archive.sh` and `100755` index modes for the two
    existing scripts.
  - Applied with `git apply --cached` onto an index where master's scripts are 100755, all three scripts end up
    100755, with no mode warnings.
  - The commit must use the patch (or `git apply --index`). A Windows working-tree copy would still lose the bit.
- **Stale rolling asset (P4, closed).**
  - Without `DATA_ASSET`, restore reads each asset's `updatedAt`. It restores the newest dated copy when that copy
    is strictly newer than `football-db.tar.zst`, or when the rolling asset is absent.
  - Publish uploads the dated copy before the rolling asset, so the same publish's rolling asset is never older.
    Ties keep the rolling asset.
  - ISO-8601 UTC times compare correctly as text.
  - A rolling asset that is listed but damaged still fails closed. The exit trap now prints
    `DATA_ASSET=<newest dated>`. The hint is cleared on success, and the trap keeps the original exit status.
  - An explicit `DATA_ASSET` is still honoured exactly.
- **Tests.**
  - Two new shell tests: `keeprolling` (the replacement fails and the old asset stays; restore takes the dated
    copy), and `badrolling` without deleting the file (restore fails, writes nothing, and the named asset then
    restores).
  - There are 14 shell test functions plus one 2-case parametrize, so **15 shell cases**. This matches god's
    count. All 15 are unexecuted.

## Findings (non-blocking)

- **P6. A dated copy that is listed but damaged now blocks restore.**
  - Revision 3 prefers a newer dated copy. If one was listed with a wrong size (publish then stops before
    replacing the rolling asset), every later restore picks it and fails closed.
  - In that case `asset == newest`, so no hint is printed. Before revision 3, the intact rolling asset would have
    been used.
  - It's safe, because nothing is overwritten, but it stops runs until someone acts. Recovery is
    `DATA_ASSET=football-db.tar.zst`.
  - Fix: add that line to the §6 recovery steps, or print the hint in this case too.
  - Likelihood is low: GitHub assets normally appear only once their upload completes.
- **P7. `updatedAt` from the real `gh` is untested.** The stand-in derives it from file times.
  - If the CI's `gh` doesn't output `updatedAt` for assets, each value prints as `null`, the comparison is false,
    and restore falls back to revision 2's behaviour. That degrades safely, but P4 would silently not apply.
  - Check: one read-only `gh release view data --json assets` on the CI runner, or during G3 with a scratch
    release.
- **P5 (unchanged).** The archive check is GNU-tar only. Whether the pinned `r20261028` archive passes is proven
  only by G3.

## Remaining blockers before any release candidate

1. Linux CI with zstd: all 15 shell tests green on patch `73751d56…f6a1`.
2. A commit made from the patch with the three scripts at 100755.
3. G3: migration and rollback rehearsal.
4. Provenance writers and the G4 first verified export. Gate 0 does not pass on this patch.

## Can the G3 scratch rehearsal begin? Yes

The patch is stable enough to rehearse on. Conditions:
- run on Linux with zstd; the scripts refuse to run anywhere else;
- use a copy of the pinned `r20261028` archive (`sha256:3dc27152…c69f`), checked against that digest first;
- use a stand-in `gh` or a scratch release, never the production `data` release;
- apply this exact patch hash.

The rehearsal should also cover P5 (legacy members pass `check_archive.sh`), P6 and P7.
