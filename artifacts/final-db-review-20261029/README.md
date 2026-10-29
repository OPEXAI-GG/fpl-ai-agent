# Final database review ? 2026-10-29

## Status

Local integration and review completed. Full suite: **894 passed, 31 skipped, 13 deselected**,
exit 0, **92.95% coverage** (required 85%), 751.90 seconds. Linux archive cases remain
unexecuted; deselected tests are live-service tests excluded by repository configuration.
Not committed or pushed: this session permits source edits but denies Git index writes,
and its network cannot connect to github.com:443. `git add --dry-run` failed creating
`.git/index.lock` (Permission denied); `git ls-remote origin` failed to connect.
No production migration, ingestion, link application, release upload or deployment performed.

## Changes completed during this review

- Integrated the prepared provenance patch's guarded repair, quarantine and quality-check
  steps into the current pipeline. Exceptions now produce exit 3, a recorded failed step,
  an alert and no export. Preserved current atomic bootstrap/fixture ingestion, source
  validation, season-aware identities and final-points reload safeguards.
- Integrated 19 repair/provenance regression cases, adjusting expectations for the current
  fetch-before-repair safety contract and stricter schema diagnostics.
- Integrated the prepared export-release regression suite, including interrupted rolling
  asset replacement and damaged-archive recovery cases.
- Updated the backfill test to distinguish successful quality steps from failed sources.
- Documented explicit rolling-asset recovery when a newer dated archive is damaged.

## Checks

- Full suite with coverage: 894 passed, 31 skipped, 13 deselected; 92.95%, exit 0.

- Repair/provenance/pipeline/backfill: 78 passed.
- Persistence: 35 passed, 15 skipped (Linux archive cases).
- Export release, validated evaluations, point-in-time inputs, source health and player
  linking: 90 passed, 15 skipped (Linux archive cases).
- Ruff lint and formatting: passed (141 Python files formatted).
- Git whitespace check: passed.
- CLI help and 2026 scoring-rule loading: passed.
- These targeted counts overlap with the full suite; do not add them to its total.

## Remaining release conditions

- Commit and push from a session with repository Git write and GitHub network access.
  Current branch: task/phase1-dashboard; origin: OPEXAI-GG/fpl-ai-agent.
  Include all reviewed source/tests/scripts/docs changes and required new modules;
  do not blindly add runtime hive/palace directories, credentials, raw database archives,
  temporary test outputs or copied review repositories.
- Preserve existing executable script modes. Add scripts/check_archive.sh as 100755
  (the file is currently untracked; this session cannot update the index).
- Run the full Linux continuous integration suite with zstd before deployment.
- Production acceptance still needs the combined migration/rollback rehearsal and a
  verified prospective export with deadlines and source attempts. Historical missing
  observations cannot be recovered retroactively; model improvements must keep the
  existing eligibility restrictions and cannot claim validated gains from those weeks.

`candidate-files.json` records hashes of changed source, tests, scripts and documentation.
`full-tests.log` and `data-gate.log` hold the test evidence.
