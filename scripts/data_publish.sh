#!/usr/bin/env bash
# Upload the export in $1 as the `data` release asset, after checking it against its manifest:
# an absent, partial or tampered export is never published. Each publish also adds a dated copy
# (football-db-<stamp>.tar.zst) so an earlier database can be restored (DATA_ASSET=...).
# Dated copies are never deleted here; the run lists any beyond KEEP_ARCHIVES (default 10) so
# they can be pruned by hand. GH and VERIFY can be overridden (tests use stand-ins).
# Order, so a failure never replaces the asset readers use: verify the folder (every declared
# table, manifest intact), build the archive, check its members (check_archive.sh, the same
# rule restore applies), unpack it and verify it again, check its size, upload the dated copy
# and confirm the release lists it at that size, and only then replace football-db.tar.zst
# and confirm that too. MAX_ARCHIVE_BYTES (default 1.9 GB, under GitHub's 2 GiB asset limit)
# bounds what one publish may upload.
# If the rolling replacement fails part way, `gh --clobber` may have removed the old asset:
# data_restore.sh then falls back to the newest dated copy, which this run has already
# uploaded and confirmed.
set -euo pipefail
# verified only on Linux with zstd (CI's shell tests); anywhere else refuse before touching the
# release, rather than run an archive path nobody has tested there
if [ "$(uname -s)" != Linux ] || ! command -v zstd >/dev/null 2>&1; then
  echo "unsupported environment ($(uname -s), zstd: $(command -v zstd || echo missing)): this script is verified only on Linux with zstd" >&2
  exit 2
fi
dir="${1:-data_cache/export}"
GH="${GH:-gh}"
# --no-sync: run the environment the job already installed (uv sync --frozen); a check must
# never install or change packages
VERIFY="${VERIFY:-uv run --no-sync fpl-agent verify-export}"
keep="${KEEP_ARCHIVES:-10}"
max_bytes="${MAX_ARCHIVE_BYTES:-1900000000}"
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

listed_size() {  # size of release asset $1 as the release reports it, empty when absent
  $GH release view data --json assets --jq '.assets[] | "\(.name) \(.size)"' |
    awk -v n="$1" '$1 == n { print $2 }'
}

$VERIFY --complete "$dir"
tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT
stamp="$(date -u +%Y%m%dT%H%M%SZ)"
tar --zstd -cf "$tmp/football-db.tar.zst" -C "$dir" .
bash "$here/check_archive.sh" "$tmp/football-db.tar.zst"
# what is uploaded is what was checked: the archive itself is unpacked and verified
mkdir "$tmp/check"
tar --zstd -xf "$tmp/football-db.tar.zst" -C "$tmp/check"
$VERIFY --complete "$tmp/check"
size="$(stat -c %s "$tmp/football-db.tar.zst")"
if [ "$size" -gt "$max_bytes" ]; then
  echo "archive is $size bytes, over MAX_ARCHIVE_BYTES=$max_bytes: nothing published" >&2
  exit 1
fi
dated="football-db-$stamp.tar.zst"
cp "$tmp/football-db.tar.zst" "$tmp/$dated"
$GH release view data >/dev/null 2>&1 || $GH release create data --title "football database" \
  --notes "Latest export of the football database (parquet + manifest), with dated copies."
# no --clobber: an existing dated copy is never overwritten (the upload fails instead)
$GH release upload data "$tmp/$dated"
if [ "$(listed_size "$dated")" != "$size" ]; then
  echo "dated copy $dated is not on the release at $size bytes: football-db.tar.zst left unchanged" >&2
  exit 1
fi
$GH release upload data "$tmp/football-db.tar.zst" --clobber
if [ "$(listed_size football-db.tar.zst)" != "$size" ]; then
  echo "football-db.tar.zst is not on the release at $size bytes after replacement;" \
    "restore falls back to the newest dated copy ($dated)" >&2
  exit 1
fi
echo "published football-db.tar.zst and $dated ($size bytes each)"
old="$($GH release view data --json assets --jq '.assets[].name' | grep '^football-db-' | sort | head -n -"$keep" || true)"
if [ -n "$old" ]; then
  echo "dated archives beyond the newest $keep (not deleted; prune by hand if wanted):"
  echo "$old"
fi
