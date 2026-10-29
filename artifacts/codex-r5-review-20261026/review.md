# Codex r5 review — 2026-10-26

**Approved for local database-backed model development on isolated copies.** The two r4 correctness blockers are closed on the tested candidate. The scoped DB08 transition gate is accepted; broad production/hosting assurance and live operation are not approved. Do not restart the storage audit without a new demonstrated defect.

## Independent verification

- Source/test/rules files exactly match Claude's final manifest: `875fa1a59859dbfce6fc8fb4dfac0b382df602f3392ae4c9689a791291c1e33e`. A frozen review copy was made and source hashes stayed unchanged through review (`checkpoint.json`, `end-stability.json`).
- Focused regression run on that copy: `tests/test_validated_eval.py tests/test_source_health.py`, **27 passed in 43.21 s, exit 0**. Exact command/output in `result.json` and `tests.log`. Installed Git Bash selected for the unsupported-platform script check; this is not Linux archive verification.
- Independent r4 reproductions, adapted only to include canonical match records and supply the new replay cutoff argument: both entrypoints retain the same 1,938-row fit history after late snapshots, zero positions changed; zero late-price changes; zero false cross-season conflicts or exclusions (`probes.py`, `probes.json`).
- Synthetic end-to-end dry smoke passed, exit 0: enabled dry planning, `is_live=False`, all recorded decisions dry, explicit isolated output/snapshot paths, network/auth/notifications forbidden (`dry_smoke.py`, `dry-smoke.json`, `dry-smoke.log`). Fake FPL lacks the ownership standings endpoint; the expected degradation was logged. This is integration evidence, not a real-team recommendation or forecast-quality result.
- Inspected all 12 final batch logs: **814 passed, 5 skipped**, every batch exit 0. Independently recalculated the saved coverage on a private copy: **93%, exit 0** (`coverage-report.txt`). Full suite was not rerun independently; it is evidence tied to the verified source fingerprint.
- Production hash remains `9bc925c102c1bc108948de1ba9acd153d07f09550a02a7b7ec8780fa66305e1d`. Production was not opened, migrated or refreshed. No application implementation changed by Codex.

## Why the blockers are closed

`project.history_as_of` now applies historical-gameweek, kickoff and snapshot cutoffs before evaluation fits and baselines. Both entrypoints pass `known_at_odds=validated`; validated replay uses the cutoff-bound pool and preserves observed availability. The focused tests cover late fixtures, fit reuse, pool changes and odds behavior.

`quality.FPL_FIXTURE_MATCHES` binds source fixture IDs to the canonical match season and deduplicates legacy/season aliases. Both conflict detection and exclusion mapping use it. Regressions cover reused IDs, real conflicts in either season, dual aliases and mislabelled matches.

## Approval boundaries and next work

Proceed to the model phase on frozen, isolated inputs with explicit eligibility manifests. Current historical data does not support a blanket retrospective decision-performance claim. Snapshot/odds cutoff tests do not establish observation-time completeness for every upstream statistic or later correction. Keep reconstructed and missing inputs labelled, and accumulate genuinely prospective evidence before claiming better FPL returns.

Linux archive operation remains unverified here; keep it outside this approved workflow pending CI evidence. F-16 observations remain unresolved and excluded by the reviewed evaluation path. The previously reported dependency advisory remains a separate approval/verification item. Production apply, release publishing, persistent configuration changes and live FPL actions remain unauthorized.

Next: freeze a reproducible baseline and per-feature coverage/uncertainty matrix; then run a bounded FPL-stat fallback experiment for players currently dependent on priors. Preserve the baseline and predeclare evaluation metrics. Ownership should influence differentiated squad decisions only after expected output and uncertainty are estimated; popularity is not evidence of performance. No candidate promotion based solely on an exploratory replay.
