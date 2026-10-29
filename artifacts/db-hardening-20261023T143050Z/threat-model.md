# U04 / DB03 threat model and issue register (Claude, 2026-10-23; awaiting Codex)

Supported scope: **local, single user, one machine**. No hosting, no multi-user auth. This is not a
"secure" claim; it lists what was examined and what was not.

## Trust boundaries and inputs
| Input | Reaches | Control | Evidence |
|---|---|---|---|
| Public feeds (FPL, Understat, football-data, FotMob, vaastav) | ingest_* writers | contracts.* shape checks; values bound as `?` params; identifiers are code constants | grep of `execute(f…)` in src: every interpolation is a constant, a `?` list or a declared table name |
| Export folder / release archive (`manifest.json`, `*.parquet`) | `verify_export`, `import_parquet` | **fixed this batch**: manifest must be an object, only declared table names (a name is also a file path: `../escape` refused), file columns must equal manifest columns and be declared, import by column name | `test_malformed_or_unknown_manifest_entries_are_refused` (5 cases), `test_a_file_with_an_undeclared_column_is_refused`, `test_reordered_columns_load_by_name` |
| Legacy export without manifest | `import_parquet` (allowed unless `require_manifest`) | only declared table files read; undeclared columns refuse the import atomically | same tests |
| `data_restore.sh` tar archive | temp dir, then `cp -R` | `tar` into fresh `mktemp -d`; verify-export when a manifest exists | **not run here** (Windows; needs bash+zstd). Open: symlink members are copied as symlinks by `cp -R`; a manifest-less legacy archive skips verification. Requires write access to the repo's `data` release. |
| Dashboard HTTP (localhost) | in-memory snapshot; job runner | Host allow-list (421), fixed routes (404), per-start token + Origin for controls, static allow-list, read-only DB open, `X-Frame-Options: DENY`, `nosniff` | existing `test_only_its_own_host_and_routes_are_served`, controls tests; players search/sort are in-memory (no SQL) |
| CLI args (`--db`, `--export`, paths) | local files | the operator's own input; not a boundary in single-user scope | — |
| Env/settings | `Settings.from_env`, dashboard `settings_presence` | presence only, never values | existing test that the refresh token never reaches the page; **no token file was read in this batch** |

## Issue register
| ID | Severity | Status | Finding |
|---|---|---|---|
| S1 | medium | fixed | Export manifest shape/table names unchecked: `[]`/`{"tables": []}`/non-dict entries raised AttributeError instead of refusing; unknown/`../` names accepted by `verify_export` (only read; import iterated declared tables). |
| S2 | medium (integrity) | fixed | Positional `SELECT *` import: a reordered or older file put values into the wrong columns or crashed mid-transaction. |
| S3 | low | open (approval) | `certifi 2026.7.22` — PYSEC-2026-4320, fixed in 2026.10.19 (`pip-audit 2.10.1`, 2026-10-23T15:00Z, 38 frozen packages, `--no-deps`, OSV/PyPI service). No upgrade without approval. |
| S4 | low | open | `data_restore.sh`: symlinks in an archive survive `cp -R`; legacy (manifest-less) archives are not verified. Suggest `cp -R --no-dereference` refusal of symlinks / `find -type l` check, and requiring a manifest after the next publish. Not changed (shell path untestable here). |
| S5 | info | open | Private manager state (`decisions`, `runs.entry_id`, `reviews`) is exported to the `data` release with everything else. Single-user scope: acceptable; a multi-user product must split it (C06). |
| S6 | info | open | `scout_cache`/`field_ownership` keys are not per-customer — a multi-user blocker (C06), not a single-user issue. |

## Not examined
Network TLS behaviour of the HTTP clients; GitHub Actions workflow permissions; browser-side JS of the dashboard
beyond the existing tests; Linux shell scripts (no bash/zstd here); OS file permissions of `data_cache/`.
