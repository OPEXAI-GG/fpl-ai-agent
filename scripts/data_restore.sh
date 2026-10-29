#!/usr/bin/env bash
# Download the football database export from the `data` release into $1 (default
# data_cache/export). Only a release with no export at all means "first run, start empty"; an
# auth, network, download, unpack or checksum failure stops the job, so a broken restore can
# never be mistaken for an empty database and published over the good one.
# DATA_ASSET picks an older dated archive (football-db-<stamp>.tar.zst) to roll back to.
# Without it, the rolling football-db.tar.zst is used, unless the newest dated copy is newer
# (the release's updatedAt): a publish uploads its dated copy first, so a dated copy newer than
# the rolling asset, or a rolling asset that is absent, means a publish stopped before replacing
# it, and the dated copy is the latest good export.
# Recovery when the rolling asset is listed but damaged (an interrupted replacement): restore
# fails closed and names the dated copy; rerun with DATA_ASSET=<that name>, then publish again
# to replace the rolling asset.
# GH and VERIFY can be overridden (tests use stand-ins).
# The archive must pass check_archive.sh (only manifest.json and <table>.parquet files at its
# top level) before it is unpacked. A legacy archive without a manifest is accepted if it holds
# at least one table. Files reach $1 only complete: they are assembled in a sibling folder that
# is renamed into place.
set -euo pipefail
# verified only on Linux with zstd (CI's shell tests); anywhere else refuse before touching the
# release, rather than run an archive path nobody has tested there
if [ "$(uname -s)" != Linux ] || ! command -v zstd >/dev/null 2>&1; then
  echo "unsupported environment ($(uname -s), zstd: $(command -v zstd || echo missing)): this script is verified only on Linux with zstd" >&2
  exit 2
fi
dir="${1:-data_cache/export}"
asset="${DATA_ASSET:-football-db.tar.zst}"
GH="${GH:-gh}"
# --no-sync: a check must never install or change packages
VERIFY="${VERIFY:-uv run --no-sync fpl-agent verify-export}"
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# the name is also a download pattern: only the rolling asset or a dated copy
if ! [[ "$asset" =~ ^football-db(-[0-9]{8}T[0-9]{6}Z)?\.tar\.zst$ ]]; then
  echo "DATA_ASSET must be football-db.tar.zst or football-db-<YYYYMMDDTHHMMSSZ>.tar.zst" >&2
  exit 1
fi
if [ -d "$dir" ] && [ -n "$(ls -A "$dir")" ]; then
  echo "refusing to restore into non-empty $dir" >&2
  exit 1
fi
tmp="$(mktemp -d)"
stage=""
hint=""  # printed if the restore fails after an asset was chosen
trap 'st=$?; rm -rf "$tmp"; [ -z "$stage" ] || rm -rf "$stage"
  [ "$st" -eq 0 ] || [ -z "$hint" ] || echo "$hint" >&2' EXIT

if ! listing="$($GH release view data --json assets --jq '.assets[] | "\(.name) \(.updatedAt)"' 2>"$tmp/err")"; then
  if grep -qi "release not found" "$tmp/err"; then
    echo "no data release yet; starting empty"
    exit 0
  fi
  echo "could not read the data release: $(head -c 300 "$tmp/err")" >&2
  exit 1
fi
listed() { awk -v n="$1" '$1 == n { f = 1 } END { exit !f }' <<<"$listing"; }
updated() { awk -v n="$1" '$1 == n { print $2 }' <<<"$listing"; }
newest="$(awk '{ print $1 }' <<<"$listing" | grep -E '^football-db-[0-9]{8}T[0-9]{6}Z\.tar\.zst$' | sort | tail -n 1 || true)"
if [ -n "${DATA_ASSET:-}" ]; then
  if ! listed "$asset"; then
    echo "asset $asset is not on the data release" >&2
    exit 1
  fi
elif ! listed "$asset"; then
  if [ -z "$newest" ]; then
    echo "data release has no export yet; starting empty"
    exit 0
  fi
  echo "football-db.tar.zst is missing; falling back to the newest dated copy $newest" >&2
  asset="$newest"
elif [ -n "$newest" ] && [[ "$(updated "$newest")" > "$(updated "$asset")" ]]; then
  # ISO 8601 UTC times compare as text; the same publish's rolling asset is never older
  echo "football-db.tar.zst (updated $(updated "$asset")) is older than the newest dated copy" \
    "$newest (updated $(updated "$newest")): a publish stopped before replacing it;" \
    "restoring the dated copy" >&2
  asset="$newest"
fi
if [ -z "${DATA_ASSET:-}" ] && [ -n "$newest" ] && [ "$asset" != "$newest" ]; then
  hint="restore of $asset failed; to restore the newest dated copy instead rerun with DATA_ASSET=$newest"
fi

$GH release download data --pattern "$asset" --dir "$tmp"
bash "$here/check_archive.sh" "$tmp/$asset"
mkdir "$tmp/x"
tar --zstd -xf "$tmp/$asset" -C "$tmp/x"
if [ -f "$tmp/x/manifest.json" ]; then
  $VERIFY "$tmp/x"
elif ls "$tmp/x" | grep -q '\.parquet$'; then
  echo "legacy export without a manifest: accepted (the next publish adds one)"
else
  echo "archive holds no tables" >&2
  exit 1
fi
mkdir -p "$(dirname "$dir")"
stage="$(mktemp -d "$(dirname "$dir")/.restore-XXXXXX")"
cp -R "$tmp/x/." "$stage/"
[ ! -d "$dir" ] || rmdir "$dir"
mv "$stage" "$dir"
stage=""
hint=""
echo "restored $(ls "$dir" | grep -c '\.parquet$') tables into $dir from $asset"
