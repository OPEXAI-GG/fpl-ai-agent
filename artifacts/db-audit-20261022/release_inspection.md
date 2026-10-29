# Public `data` release: listing-level inspection (attributed observation)

Recorded from TASK.md "Claude response ... revision 3" (2026-10-22). Approved scope: read-only,
unauthenticated, no credentials, no restore/import/refresh. This file keeps that evidence in the audit
folder; Codex could not reach the public API URL in its own review, so this is **attributed evidence, not
independently verified**.

- Repository `OPEXAI-GG/fpl-ai-agent` (public; `origin`). Release `data`, id 407367915, created
  2026-10-08T20:06:51Z, published 2026-10-09T00:16:18Z, not draft or prerelease, target `master`.
- Exactly one asset: `football-db.tar.zst`, id 665783740, 188,415,817 bytes, updated 2026-10-22T12:17:47Z-54Z,
  0 downloads, GitHub digest `sha256:831d1c13...e77e2f`. A bounded download (cap 200 MB) matched that digest.
- `tar -tvf` lists 21 `*.parquet` members and no `manifest.json` (legacy export by `database.py` definition).
  Seven of the 28 tables are absent: `league_translation`, `match_odds_log`, `news_evidence`, `runs`,
  `scout_cache`, `scout_calls`, `transfers`.
- **Correct wording:** one export version observed; no dated copies were observed on the release. Historical
  row/season coverage and known-at provenance are **not yet inspected** (the earlier phrase "no remote long
  history exists in this release" is withdrawn: one current export can hold many seasons).
- **Not done in this pass:** the authorized bounded Parquet footer inspection (row counts, row groups,
  season/timestamp statistics) was not run; the previously downloaded archive is not retained in the
  workspace. Footer statistics, when read, show min/max only and never prove known-at provenance.
