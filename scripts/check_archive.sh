#!/usr/bin/env bash
# Refuse an export archive ($1, .tar.zst) holding anything but the `./` folder and top-level
# regular files named manifest.json or <table>.parquet: no links, subfolders, paths or other
# files. Publish and restore both run it, so whatever one accepts the other accepts too.
# Reads GNU tar's verbose listing (mode owner size date time name); another tar's listing does
# not match and is refused, never accepted.
set -euo pipefail
archive="$1"
list="$(tar --zstd -tvf "$archive")"
while read -r mode _ _ _ _ name; do
  case "$mode" in
    d*) if [ "$name" = "./" ] || [ "$name" = "." ]; then continue; fi ;;
    -*) if [[ "$name" =~ ^(\./)?(manifest\.json|[a-z0-9_]+\.parquet)$ ]]; then continue; fi ;;
  esac
  echo "archive member not allowed: $mode $name" >&2
  exit 1
done <<<"$list"
