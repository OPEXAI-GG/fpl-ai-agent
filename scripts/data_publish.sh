#!/usr/bin/env bash
# Upload the export in $1 as the `data` release asset, after checking it against its manifest:
# an absent, partial or tampered export is never published. Each publish also adds a dated copy
# (football-db-<stamp>.tar.zst) so an earlier database can be restored (DATA_ASSET=...).
# Dated copies are never deleted here; the run lists any beyond KEEP_ARCHIVES (default 10) so
# they can be pruned by hand. GH and VERIFY can be overridden (tests use stand-ins).
set -euo pipefail
dir="${1:-data_cache/export}"
GH="${GH:-gh}"
VERIFY="${VERIFY:-uv run fpl-agent verify-export}"
keep="${KEEP_ARCHIVES:-10}"

$VERIFY "$dir"
tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT
stamp="$(date -u +%Y%m%dT%H%M%SZ)"
tar --zstd -cf "$tmp/football-db.tar.zst" -C "$dir" .
cp "$tmp/football-db.tar.zst" "$tmp/football-db-$stamp.tar.zst"
$GH release view data >/dev/null 2>&1 || $GH release create data --title "football database" \
  --notes "Latest export of the football database (parquet + manifest), with dated copies."
$GH release upload data "$tmp/football-db-$stamp.tar.zst"
$GH release upload data "$tmp/football-db.tar.zst" --clobber
echo "published football-db.tar.zst and football-db-$stamp.tar.zst"
old="$($GH release view data --json assets --jq '.assets[].name' | grep '^football-db-' | sort | head -n -"$keep" || true)"
if [ -n "$old" ]; then
  echo "dated archives beyond the newest $keep (not deleted; prune by hand if wanted):"
  echo "$old"
fi
