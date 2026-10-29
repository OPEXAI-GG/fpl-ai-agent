# Published database verification — FAA-7

Checked 2026-10-26, approximately 19:30–19:32 UTC. Owner: Zaza.

**Result: release metadata verified. Supplied Parquet tables were analyzed in scratch, but their provenance is unverified because the pinned archive and manifest were not present.** No production writes, source edits, database migrations or local development database queries were performed.

## Published identity

Authenticated requests through the hive GitHub integration returned:

| Field | Value |
| --- | --- |
| Repository | `OPEXAI-GG/fpl-ai-agent` (public), default branch `master` |
| Release | [`data`](https://github.com/OPEXAI-GG/fpl-ai-agent/releases/tag/data), ID `407367915` |
| Release published / updated | `2026-10-09T00:16:18Z` / `2026-10-26T13:29:27Z` |
| Asset | `football-db.tar.zst`, ID `678256795` |
| Asset created / updated | `2026-10-26T13:29:18Z` / `2026-10-26T13:29:27Z` |
| Asset size | `193374019` bytes |
| GitHub-reported SHA-256 | `5a696ae511a919854339554fcb67cad8ac078f6e66ddd4c08e468ab3adf90f48` |
| Download | [Published archive](https://github.com/OPEXAI-GG/fpl-ai-agent/releases/download/data/football-db.tar.zst) |

The digest above is **reported by GitHub, not locally verified**. The expected archive `artifacts/FAA-7-production/football-db.tar.zst` was absent when checked after the user said the data was added. Instead, the folder contained 21 loose Parquet files (total 211,122,658 bytes), dated 2026-10-27 around 16:00 UTC, and no archive or `manifest.json`. Their individual hashes are saved in `parquet-input-hashes-before.json` and `parquet-input-hashes-after.json`; all 21 hashes remained unchanged. These files can be analyzed, but they cannot be tied cryptographically to the release asset from available evidence. The release response lists only this one asset; no dated rollback archive was listed at observation time.

## Access evidence and exact limitation

1. Direct authenticated PowerShell GitHub API request failed: `Unable to connect to the remote server`.
2. Broker discovery succeeded and returned integration ID `github`. Authenticated repository, release, asset metadata and workflow-run requests succeeded. Missing `gh` is not the blocker.
3. Requesting `/repos/OPEXAI-GG/fpl-ai-agent/releases/assets/678256795` through the broker with `Accept: application/octet-stream` returned 1,642 bytes of JSON asset metadata, not the 193 MB archive. The response was renamed `asset-api-response.json`; it is not a database download. Its local SHA-256 is `46abd084d1a97abd14ac492fe0924f2dabc5fdba62e79b1ebc3dc778faf3496f`.
4. Read-only inspection of the running packaged Munder Difflin v0.5.7 confirms its GitHub authentication mode injects `accept: application/vnd.github+json` after copying caller headers, replacing the caller's Accept value. This explains the metadata response. No app or connection configuration was changed.
5. A direct Node fetch using the available token failed with `TypeError fetch failed`, cause `EACCES`. No credentials were printed or saved.
6. The user then placed 21 loose Parquet files into the evidence folder. Their hash set is stable before and after analysis, but without the archive or manifest it cannot establish that they are byte-for-byte contents of the pinned asset. The command-line export verifier could not authenticate this loose folder because its required `manifest.json` is absent.

To resume, supply the original asset through an authorized download-capable channel into `artifacts/FAA-7-production/football-db.tar.zst`, or provide a supported GitHub connection that preserves the octet-stream Accept header. Verify size and SHA-256 against the pinned identity above before unpacking. If the mutable asset has changed, refresh and record its new identity first.

## Diagnostics

| Requested check | Status |
| --- | --- |
The supplied Parquet files were imported into `scratch-materialized/supplied-tables.duckdb`. The standard importer stalled on large tables during its multiset difference against empty tables, so it was stopped and a separate simple Parquet-to-empty-scratch load was used. The original Parquet files were not modified. Of the 34 declared tables, 21 files were supplied and these 13 absent tables were created empty in the scratch database: `fpl_deadlines`, `migration_log`, `player_codes`, `repair_log`, `source_attempts`, `transfers`, `league_translation`, `match_odds_log`, `scout_cache`, `scout_calls`, `runs`, `news_evidence`, and `player_links`. `scratch-materialized/parquet-schema-shapes.json` separately compares each present Parquet file before table creation: all 21 supplied files have no missing, extra, or differently typed columns against current declarations.

| Requested check | Result on supplied files (provenance unverified) |
| --- | --- |
| `db-schema` on materialized scratch | Current (`missing_tables`, `extra_tables`, `column_differences` empty). Initialization created 13 absent tables. The command therefore describes the materialized schema, while raw Parquet fields are separately compared above. |
| `source-health --season 2026` | Degraded, exit 1. Three critical FPL records (`bootstrap`, `fixtures`, `players`) say `never` / no recorded attempt. Snapshot age 0.47 hours; points check `ok`; FotMob detail 80/80 finished matches. |
| `db-eligibility --season 2026` | Eligible: none. Reconstructed: GWs 6–8. Unavailable: GWs 1–5. 3 matches excluded; 4 source conflicts reported. |
| `db-links --season 2026` | 366/451 players linked (81.15%); linked minutes 131,188/157,372 (83.36%); 85 unlinked. |

The schema command's zero exit does not prove the original release's schema because the scratch database was populated under current declarations. Source-health's “never attempted” evidence may also reflect the supplied `ingest_state` rather than the actual production workflow. All figures in this table describe only the provided loose files until their archive identity is established.

Production schema, freshness, coverage, linked minutes, history and eligibility remain unknown. The development database's 70.3% baseline does not establish any production result.

The published format is a Parquet export archive, not a raw DuckDB database. After access is restored, preserve the archive, unpack into a new scratch directory, verify any manifest, and materialize its tables into a separate scratch DuckDB file without adding current schema tables. Run the reports read-only and compare hashes before/after. Any compatibility migration must use another explicitly labelled scratch copy, with its results separated from original-schema results.

## Release readiness evidence

The three latest data workflow runs returned `completed/success`, all at commit `51f0defa364244ae5faca202ccb4f0ba648350d1`. The latest is [run 40330744183](https://github.com/OPEXAI-GG/fpl-ai-agent/actions/runs/40330744183), created `2026-10-26T13:27:35Z`, updated `2026-10-26T13:29:31Z`. Its timing is consistent with the asset upload, but run success alone does not prove database quality or that current local changes were deployed.

Local `scripts/data_restore.sh` and `scripts/data_publish.sh` support manifest verification and dated rollback archives; both explicitly require Linux with zstd. They were inspected, not executed. The observed release has no dated archive, so rollback availability is not demonstrated. This is an operational observation, not a duplicate of Wegz's FAA-3 code audit. No release approval is implied.

## Saved evidence

Under `artifacts/FAA-7-production/`:

- `repository.json`, `release-metadata.json`: authenticated GitHub identity responses.
- `asset-api-response.json`: the metadata response received instead of archive bytes.
- `data-workflow-runs.json`: latest three workflow-run responses.
- `access-diagnostics.json`: sanitized request outcomes and remaining checks.
- `broker-header-inspection.txt`, `inspect-broker.cjs`: packaged-app header behavior and reproduction helper.
- `download.cjs`: direct read-only download attempt helper; reads credentials from the environment and contains no secret.
- `scratch-materialized/supplied-tables.duckdb`: scratch database materialized from supplied Parquet files.
- `db-schema.json`, `source-health.json`, `db-eligibility.json`, `db-links.json`: requested command outputs; exit codes in `diagnostic-exit-codes.json`.
- `scratch-materialized/parquet-schema-shapes.json`, `parquet-input-hashes-before.json`, `parquet-input-hashes-after.json`, `input-integrity.json`: direct schema compatibility and unchanged input hashes.

FAA-7 remains blocked on verifying the supplied tables against the release identity. The original asset is still absent; to complete production verification, provide the pinned archive or manifest-backed export and verify its recorded size and digest. Zaza owns repository hygiene, GitHub workflows, continuous integration evidence, deployment/rollback readiness and production artifact verification; god owns branch integration and release authorization.

## Update 2026-10-28: published build verified in scratch (Zaza)

The 2026-10-26 pin was superseded when the rolling `data` release was republished. The current asset 685637934 (195,782,284 B, `sha256:3dc27152…c69f`, 2026-10-28T16:37:13Z, built from `51f0defa3642` per run timing) was received and its bytes verified. Scratch results: schema shapes match (13 declared tables are absent from the export, including `player_links` and `source_attempts`); source-health is degraded (the export lacks attempt records); **no eligible 2026 GWs** (6–8 reconstructed, 1–5 unavailable); **production link coverage is 83.36% of minutes** (the reviewed development copy shows 97.40%). Full evidence and limits: `artifacts/FAA-7-production/r20261028/README.md`.
