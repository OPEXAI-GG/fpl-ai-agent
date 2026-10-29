# FAA-7 archive receipt check — 2026-10-28

Checked by Zaza (`zaza-mvsc3unw`) around 20:15 UTC. This was read-only: nothing was moved, copied,
extracted or overwritten.

## Result: the file does NOT match the pinned release. Verification stopped.
| | Pinned (`release-metadata.json`, `asset-api-response.json`) | Received |
| --- | --- | --- |
| Path | `artifacts/FAA-7-production/football-db.tar.zst` | **not there.** The file is at `C:\Users\bedor\Downloads\football-db (1).tar.zst` (saved 2026-10-28 20:01:05Z) |
| Bytes | 193,374,019 | **195,782,284** |
| SHA-256 | `5a696ae5…0f48` | **`3dc27152…c69f`** |
| Asset updated | 2026-10-26T13:29:27Z | the files inside are dated **2026-10-28 16:36:59–16:37:02Z** |

The file is a real zstd-compressed tar, not a ZIP: it starts with the zstd magic bytes
`28 b5 2f fd`. I streamed its file list without extracting. It holds 22 Parquet tables
(`player_match_stats.parquet` is 145 MB; also matches, appearances, fpl_*_gw, projections,
ingest_state, decisions and others).

## Interpretation (inference)
The `data` release tag gets republished, so its download URL serves the newest asset. The human
most likely downloaded a newer build (from 2026-10-28 around 16:37Z) that replaced the pinned
2026-10-26 asset. The " (1)" in the name suggests an earlier download with the same name exists
or existed in Downloads; none is there now. I did not check the live release metadata, because
this session's network is limited to the local broker.

## Not done
No scratch materialisation, schema, health or coverage checks were run on this file. The earlier
results in `scratch-materialized/` are unchanged. Production was not touched.

## Decision needed (god or the human)
1. Re-pin to this newer asset: fetch the current release metadata, confirm the digest equals
   `3dc27152…`, then run the scratch-only verification against the new pin. Or
2. Get the exact pinned bytes (`5a696ae5…`). This is probably impossible if the release asset was
   replaced.
