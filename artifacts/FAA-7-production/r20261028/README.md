# FAA-7 / Gate 0: published database verified in scratch (2026-10-28 build)

By Zaza (`zaza-mvsc3unw`), 2026-10-28 between 20:15 and 20:35 UTC. All work was scratch-only and
read-only. Production, the release and `data_cache/football.duckdb` were not touched. The
downloaded original (`C:\Users\bedor\Downloads\football-db (1).tar.zst`) was left in place.

## 1. Archive identity: verified
- **The 2026-10-26 pin is superseded.** The old asset (id 678256795, 193,374,019 B,
  `5a696ae5…0f48`) is no longer published.
- The current release `data` asset **685637934**, `football-db.tar.zst`, updated
  2026-10-28T16:37:13Z, is 195,782,284 B, `sha256:3dc271523c56637463a91a00d1b149e2acef399406004b94ce9898b15127c69f`
  (`release-metadata-20261028.json`, fetched read-only via the GitHub broker).
- The downloaded file and the copy `football-db-20261028.tar.zst` both match those bytes and that
  digest exactly. Format: zstd-compressed tar (magic `28 b5 2f fd`).
- Builder: the asset time matches the scheduled **`plan`** workflow run
  [40727258853](https://github.com/OPEXAI-GG/fpl-ai-agent/actions/runs/40727258853) (16:34:15 to
  16:37:17Z, success) at commit **`51f0defa3642`** on `master`. This is an inference from timing.
  Local work sits on `task/phase1-dashboard` at `c6a0c5f` plus uncommitted changes, so **production
  does not run the current local code.**
- The archive has 21 Parquet files and 211,977,446 B unpacked. It has **no manifest file**, so
  `verify-export` cannot be applied. Extraction used Python `tarfile` with `filter="data"` (path-safe).

## 2. Results (scratch DB `scratch.duckdb`, loaded under the current declared schema)
| Check | Result |
| --- | --- |
| Parquet shapes against declared schema | 21/21 present files match exactly (no missing, extra or retyped columns). 13 declared tables are **not in the export**: transfers, league_translation, match_odds_log, scout_cache, scout_calls, runs, news_evidence, player_codes, migration_log, repair_log, **source_attempts**, fpl_deadlines, **player_links**. |
| `db-schema` | current, exit 0. This describes the scratch DB after the 13 empty tables were created, not the release's own schema. |
| `source-health --season 2026` | **degraded**, exit 1. fpl:bootstrap/fixtures/players say "never (no recorded attempt)". The FPL snapshot is 3.8 h old (16:36:49Z, ok); points ok; FotMob detail 80/80 finished matches. |
| `db-eligibility --season 2026` | **Eligible: none.** Reconstructed: GWs 6–8. Unavailable: GWs 1–5 (approximate deadlines, no pre-deadline snapshot). 3 excluded matches, 4 conflicts. |
| `db-links --season 2026` | 366/451 players linked (81.15%), minutes 131,188/157,372 (**83.36%**), all by name; 0 by evidence; 85 unlinked ("no FotMob player with this name"). |
| Freshness | Points to 2026 GW8; player snapshots to GW9; projections to GW15; 36 decisions recorded. |
| Integrity | Parquet hashes are identical before and after; the scratch DB hash is identical before and after the four reports. |

Row counts are in `materialize-rows.json`. Full outputs: `db-schema.json`, `source-health.json`,
`db-eligibility.json`, `db-links.json`; exit codes in `diagnostic-exit-codes.txt`.

## 3. What this means for Gate 0
- **Production has no eligible point-in-time evidence for 2026.** This matches the M00 finding on
  the development copy.
- **Production link coverage is 83.36%,** against 97.40% on the reviewed development copy. The
  release carries no `player_links` table, so the FAA-3 evidence links are not in production. The
  development 97.40% must not be quoted as a production figure.
- The "never attempted" health states come from the export, which leaves out `source_attempts`.
  They are an export gap, not proof that production never fetched FPL data (the snapshot is 3.8 h old).
- `token-check` scheduled runs on 2026-10-28 (09:59Z, 18:28Z) **failed**
  (`workflow-runs-20261028.json`). The FPL sign-in token is probably dead. I did not investigate
  further and took no account actions.

## 4. Limits
- The 2026-10-26 pinned bytes were never verified. These results are for the 2026-10-28 build only.
- The export has no manifest, so I can't prove the export is complete beyond the 21 files.
- Loading under the current schema adds 13 empty tables, so the schema result is not the release's
  own schema.
- I did not run a migration, a model, or any test. Earlier results in `../scratch-materialized/` and
  `../*.json` are preserved unchanged.
- The rolling `data` release has no dated archive, so rollback availability is still not
  demonstrated.
