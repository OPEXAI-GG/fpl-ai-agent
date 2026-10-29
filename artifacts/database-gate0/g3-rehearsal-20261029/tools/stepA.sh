. "$(dirname "$0")/env.sh"; set -uo pipefail
src=/mnt/c/Users/bedor/OneDrive/Documents/FPL/fpl-ai-agent-1/artifacts/FAA-7-production/r20261028/football-db-20261028.tar.zst
mkdir -p "$G3/w"
# stand-in release = production's state on 2026-10-28: rolling asset updated 16:37:13Z. Production has no
# dated copy; a stand-in dated copy of the same bytes is added so rollback to the old archive can be shown.
cp "$src" "$FAKE_STORE/football-db.tar.zst"; cp "$src" "$FAKE_STORE/football-db-20261028T163713Z.tar.zst"
touch -d 2026-10-28T16:37:13Z "$FAKE_STORE"/*.tar.zst
sha256sum "$FAKE_STORE"/*.tar.zst
echo "== P5: check_archive.sh on the pinned legacy archive"; bash "$SCRIPTS/check_archive.sh" "$FAKE_STORE/football-db.tar.zst"; echo "exit $?"
tar --zstd -tvf "$FAKE_STORE/football-db.tar.zst" > "$G3/ev/legacy-listing.txt"; wc -l < "$G3/ev/legacy-listing.txt"; head -3 "$G3/ev/legacy-listing.txt"
echo "== restore legacy (patched data_restore.sh, no DATA_ASSET)"; bash "$SCRIPTS/data_restore.sh" "$G3/w/legacy"; echo "exit $?"
ls "$G3/w/legacy" | wc -l; ls "$G3/w/legacy" | grep -v parquet || true
