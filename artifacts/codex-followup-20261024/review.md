# Codex follow-up review — 2026-10-24

Verdict: **Changes needed for U06; original two reproductions resolved. DB08 remains open.**

## Findings and accepted scope

- **High, U06: own-gameweek snapshots still bypass the cutoff.** `src/fpl_agent/model/features.py:91` chooses the latest snapshot without an observation cutoff; `:54` then excludes rows tagged `snapshot` from re-derivation. On synthetic data, a historical GW1 row has MID from an eligible GW2 snapshot. Adding a GW1 DEF snapshot observed after the cutoff changes the freshly rebuilt, cutoff-filtered row from MID to DEF. `project.py:250` passes this frame into model history. Apply the cutoff to own-gameweek metadata selection as well as the season fallback; preserve row identity and observation provenance. Test late initial snapshots and later corrections, mixed players, repeated cutoffs and the actual projection/history path. Keep price/status/team unknown when no eligible evidence exists. The original other-gameweek disagreement now correctly remains MID.
- **U03/U04 original manifest bypass resolved.** Independently removing the teams manifest entry now yields an explicit verification error, raises ExportError and imports zero rows. Import also restricts iteration to the manifest table set. This accepts the specific fix, not the entire persistence/security gates. Claude's declared verify/import race and remaining recovery criteria remain open.
- **U02 opt-in implementation inspected.** Both option dataclasses default repairs to false; both CLI entrypoints require `--repairs` and let `--no-repairs` win. No persistent config or workflow change was needed. Journal rollback idempotence and constructor-write reconciliation are still separate unfinished criteria.
- **U05 outcome record implemented, acceptance remains partial.** Source attempts are written in their own transaction after source work, and the failure test now preserves data and successful-ingest markers while allowing the attempt row. Dashboard visibility and the remaining source-health criteria are not complete. Query-string stripping is verified by its test; it is not a comprehensive proof that arbitrary exception text cannot contain private data.
- **Documentation correction needed:** README.md:39,100 and pipeline.py:62,98 still imply failed runs write nothing. Attempt rows are deliberately written, and final quarantine checking happens after successful source transactions have committed. Document source-level atomicity, operational records and no-export-on-bad-data precisely; do not imply whole-update rollback.

## Evidence

Frozen copy: `repo/`; source/test/rules hashes captured and verified before/after copying. Fingerprint `07c25b75c36a08f1d831511db983cda40bf4f1baccfeda6ec07a5660a93114ed`; HEAD and per-file hashes in `checkpoint.json`. Captured source files remained unchanged when checked during review.

Independent probes: `.venv/Scripts/python.exe artifacts/codex-followup-20261024/probes.py` (exit 0), results `probes.json`. Original leakage: MID to MID; newly tested own-gameweek case: MID to DEF; manifest bypass refused, zero imported rows. All data synthetic and isolated. Probe exits successfully to record both fixed and failing observations, not to assert all acceptance gates passed.

Independent lint: frozen copy `python -m ruff check src tests`, exit 0, `lint.log`. Focused pytest (minutes, persistence, pipeline, backfill): **107 passed, 4 skipped, 107.85 s, exit 0**, recorded in `result.json` and `tests.log`. Source hashes remained unchanged through completion (`end-stability.json`). TASK.md status verdicts and continuation instructions updated after checking its original hash; `git diff --check -- TASK.md` passed.

Inspected, not independently rerun: Claude's 341 passed/5 skipped broad log, 82 passed model/pipeline log, and 33-table roundtrip report with zero differences. Full coverage gate remains unverified and was not rerun before addressing the new confirmed cutoff defect. No need to repeat broad unchanged suites now. The authorized U08 full coverage gate should run once after final fixes when resources permit; this TASK instruction is the request to do so, not a reason to wait for another permission loop.

Production file hash independently matches `9bc925c102c1bc108948de1ba9acd153d07f09550a02a7b7ec8780fa66305e1d`. No production database was opened or modified, no application implementation was changed by Codex, and no account action or notification was sent. Claude should continue authorized work through TASK.md without pausing for review.
