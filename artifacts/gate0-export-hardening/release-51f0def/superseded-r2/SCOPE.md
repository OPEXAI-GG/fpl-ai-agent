# Gate 0 export hardening — master-to-release scope map (revision 2)

Author: Pablo (pablo-mvsc2uke), 2026-10-28. Task: FAA review findings P1-P3, S1/S2 from
`artifacts/gate0-export-hardening/wegz-review.md`. Local artifact only: no commit, push,
release, publish, migration or production action. **Not production ready** (see §5).

## 1. What this is

`gate0-release-51f0def.patch` applies to production master **`51f0def`** (verified: applies
cleanly with `git -c core.autocrlf=false apply` to a fresh `git archive 51f0def` and reproduces
`tree/` byte for byte). It contains only the Gate 0 export dependency chain:

| File | Change against 51f0def | Source |
| --- | --- | --- |
| `src/fpl_agent/db/database.py` (+241 −21) | Module helpers `EXPORT_VERSION`, `MANIFEST`, `ExportError`, `_sql_path`, `_sha256`, `declared_columns`, `_file_columns`, `_undeclared`, `_manifest_tables`, `missing_tables`, `_rename`, `verify_export`; methods `export_parquet` (every declared table incl. empty, manifest, verify, staged swap, `.prev-*` kept), `_copy_tables`, `import_parquet`/`_import` (manifest verified on a private copy, by-name columns, one transaction, legacy no-manifest accepted unless `require_manifest`). Copied verbatim from the reviewed HEAD `c6a0c5f` lineage (working tree). | c6a0c5f + earlier reviewed DB-hardening |
| `src/fpl_agent/cli.py` (+25) | Only the `verify-export [--complete]` command, dispatched before settings load. | this slice |
| `scripts/data_publish.sh` | Hardened publish (§3). | this slice + earlier Linux/zstd guard |
| `scripts/data_restore.sh` | Hardened restore (§3). | this slice + earlier fail-closed restore |
| `scripts/check_archive.sh` (new) | Shared archive member allowlist (P2). | this slice |
| `tests/test_export_release.py` (new) | Export/import/verify tests and all publish/restore shell tests, adapted from `tests/test_persistence.py`. | this slice + earlier tests |

Schema is master's **23 tables**; the manifest covers exactly those. The 11 newer tables arrive
with the provenance and identity slices.

## 2. Deliberately excluded

- `cli.py` hunks for `--repairs` (R2), `db-links --apply/--undo` (R1), `repair-undo`,
  `db-schema`, `source-health`, `db-eligibility`, backtest/replay `--validated`, the
  `created_tables` notice (S1). None are needed by export/verify/publish/restore.
- `database.py` `SchemaError`, `schema_status`, `FootballDB(schema=)`, link/identity code,
  repair journals, `fpl_deadlines`/`source_attempts`/`match_odds_log` and their writers
  (provenance slice, Nabil, separate review).
- Untracked modules (`db/eligibility.py`, `source_health.py`, `link_audit.py`,
  `link_candidates.py`, `player_links.py`), model, dashboard, workflows. No workflow change is
  needed: every master workflow runs `uv sync --frozen` before the scripts.

## 3. Review findings resolved

| Item | Resolution |
| --- | --- |
| P1 rolling replacement | After `--clobber`, the release must list `football-db.tar.zst` at the local archive size, else exit 1 naming the dated copy. Restore, with no `DATA_ASSET`, falls back to the newest dated copy when the rolling asset is absent (the dated copy is uploaded and size-confirmed first). Tests: `badrolling` fake + fallback restore. |
| P2 same acceptance rule | `scripts/check_archive.sh` is run by publish on the built archive (before any upload) and by restore (before unpacking). Test: stray `notes.txt` refused at publish. |
| P3 dated size, test, VERIFY | Dated copy confirmed by name **and** size. The lacking-table test asserts `declared table not in the export`. `VERIFY` defaults to `uv run --no-sync fpl-agent verify-export` in both scripts (never installs; the job's `uv sync --frozen` environment is used); a test pins the default. |
| S1 scope | This patch (§1–2). |
| S2 never run | Still open: shell tests need Linux + zstd (§5). |

## 4. Local evidence (this machine: Windows, Git Bash, no zstd)

- Patched master copy, `PYTHONPATH=src`, existing `.venv`, workspace `--basetemp`:
  - `pytest tests/test_export_release.py tests/test_db.py tests/test_pipeline.py` → **50 passed,
    13 skipped** (all 13 = shell tests).
  - Full master suite with `PYTHONUTF8=1` → 466 passed, 16 skipped, 10 failed: 9 because
    `.github/` was not extracted for that run, 1 `test_helper_scripts_are_executable_and_strict`
    (Windows has no exec bit). With `.github/` extracted, `test_workflows.py` → 29 passed, 1
    failed — the same exec-bit test fails identically on **unpatched** 51f0def.
  - Without `PYTHONUTF8=1`, master's `test_backfill` vaastav test fails with a cp1252
    `UnicodeDecodeError` (Windows locale; untouched code).
- `ruff check` / `ruff format --check` clean on changed Python; `bash -n` clean on all scripts.

## 5. Remaining before any release candidate

1. **Linux CI with zstd** must run all 13 shell tests green on this exact patch (S2).
2. `scripts/check_archive.sh` must be committed with mode **100755** (patch shows 100644 because
   it was produced on Windows); `test_helper_scripts_are_executable_and_strict` checks this.
3. **Archive rehearsal (G3, Zaza):** restore the pinned `r20261028` asset
   (`sha256:3dc27152…c69f`) with the new restore — confirms its GNU tar members pass
   `check_archive.sh` as a legacy archive — import, export (23 tables + manifest), publish to a
   stand-in, restore old and new, and roll back to a dated copy.
4. Behaviour change to accept explicitly: restore now **fails closed** on auth/network/download
   errors where master's restore started empty.
5. Independent review of this exact patch hash, then god's release decision. Provenance writers
   remain a separate later slice; Gate 0 does not pass on this patch alone.
