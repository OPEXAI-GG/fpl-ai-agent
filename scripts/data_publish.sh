#!/usr/bin/env bash
# Upload the exported parquet files in $1 as the `data` release asset (replacing the previous one).
# Only called after the quality checks passed, so a bad run never overwrites good data.
set -euo pipefail
dir="${1:-data_cache/export}"
ls "$dir"/*.parquet >/dev/null
tmp="$(mktemp -d)"
tar --zstd -cf "$tmp/football-db.tar.zst" -C "$dir" .
gh release view data >/dev/null 2>&1 || gh release create data --title "football database" \
  --notes "Latest export of the football database (parquet). Replaced by every data run."
gh release upload data "$tmp/football-db.tar.zst" --clobber
