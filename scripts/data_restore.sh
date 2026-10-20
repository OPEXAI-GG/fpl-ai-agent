#!/usr/bin/env bash
# Download the football database export from the `data` release into $1 (default
# data_cache/export). Only a release or asset that does not exist yet means "first run, start
# empty"; an auth, network, download, unpack or checksum failure stops the job, so a broken
# restore can never be mistaken for an empty database and published over the good one.
# DATA_ASSET picks an older dated archive (football-db-<stamp>.tar.zst) to roll back to.
# GH and VERIFY can be overridden (tests use stand-ins).
set -euo pipefail
dir="${1:-data_cache/export}"
asset="${DATA_ASSET:-football-db.tar.zst}"
GH="${GH:-gh}"
VERIFY="${VERIFY:-uv run fpl-agent verify-export}"

if [ -d "$dir" ] && [ -n "$(ls -A "$dir")" ]; then
  echo "refusing to restore into non-empty $dir" >&2
  exit 1
fi
tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT

if ! assets="$($GH release view data --json assets --jq '.assets[].name' 2>"$tmp/err")"; then
  if grep -qi "release not found" "$tmp/err"; then
    echo "no data release yet; starting empty"
    exit 0
  fi
  echo "could not read the data release: $(head -c 300 "$tmp/err")" >&2
  exit 1
fi
if ! grep -qxF "$asset" <<<"$assets"; then
  if [ -n "${DATA_ASSET:-}" ]; then
    echo "asset $asset is not on the data release" >&2
    exit 1
  fi
  echo "data release has no export yet; starting empty"
  exit 0
fi

$GH release download data --pattern "$asset" --dir "$tmp"
mkdir "$tmp/x"
tar --zstd -xf "$tmp/$asset" -C "$tmp/x"
if [ -f "$tmp/x/manifest.json" ]; then
  $VERIFY "$tmp/x"
else
  echo "legacy export without a manifest: accepted (the next publish adds one)"
fi
mkdir -p "$dir"
cp -R "$tmp/x/." "$dir/"
echo "restored $(ls "$dir" | grep -c '\.parquet$') tables into $dir from $asset"
