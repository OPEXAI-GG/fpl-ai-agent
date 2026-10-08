#!/usr/bin/env bash
# Download the latest football database export from the `data` release into $1 (default data_cache/export).
# A missing release/asset is fine on the very first run: the database simply starts empty.
set -uo pipefail
dir="${1:-data_cache/export}"
mkdir -p "$dir"
tmp="$(mktemp -d)"
if gh release download data --pattern 'football-db.tar.zst' --dir "$tmp" 2>/dev/null; then
  tar --zstd -xf "$tmp/football-db.tar.zst" -C "$dir"
  echo "restored $(ls "$dir" | wc -l) tables into $dir"
else
  echo "no previous database export found; starting empty"
fi
