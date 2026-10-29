. "$(dirname "$0")/env.sh"; set -uo pipefail
W="$G3/w"; pub() { bash "$SCRIPTS/data_publish.sh" "$@"; echo "exit $?"; }; res() { bash "$SCRIPTS/data_restore.sh" "$@"; echo "exit $?"; }
lst() { echo "-- stand-in release:"; "$GH" release view data --json assets --jq x-updatedAt | sed 's/^/   /'; }
same() { (cd "$1" && sha256sum *) | diff -q - <(cd "$2" && sha256sum *) >/dev/null && echo "IDENTICAL: $(basename "$1") = $(basename "$2")" || echo "DIFFERENT: $1 vs $2"; }
echo "== C1 publish export1 (new, 23 tables + manifest)"; pub "$W/export1"; lst
dated1="$(ls "$FAKE_STORE" | grep -E '^football-db-2026102[9]' | sort | head -1)"; echo "dated1=$dated1"
echo "== C2 restore new (no DATA_ASSET)"; res "$W/new1"; same "$W/export1" "$W/new1"
echo "== C3 rollback to the old archive (stand-in dated copy of r20261028)"; DATA_ASSET=football-db-20261028T163713Z.tar.zst res "$W/rb-legacy"; same "$W/legacy" "$W/rb-legacy"
echo "== C4 rollback to dated1"; DATA_ASSET="$dated1" res "$W/rb-dated1"; same "$W/export1" "$W/rb-dated1"
echo "== C5 P4: rolling replacement fails (keeprolling), old rolling stays"; sleep 2
FAKE_MODE=keeprolling pub "$W/export1"; lst
echo "-- restore without DATA_ASSET"; res "$W/p4"; same "$W/export1" "$W/p4"
dated2="$(ls "$FAKE_STORE" | grep -E '^football-db-2026102[9]' | sort | tail -1)"; echo "dated2=$dated2"
echo "== C6 P6: newest dated copy listed but damaged"; sleep 2
bad="football-db-$(date -u +%Y%m%dT%H%M%SZ).tar.zst"; head -c 4096 "$FAKE_STORE/$dated2" > "$FAKE_STORE/$bad"; lst
res "$W/p6" 2> "$G3/ev/p6.stderr"; sed 's/^/   stderr: /' "$G3/ev/p6.stderr" | tail -4
grep -q DATA_ASSET "$G3/ev/p6.stderr" && echo "P6 hint printed" || echo "P6: NO recovery hint printed"
[ -e "$W/p6" ] && echo "p6 target exists: $(ls -A "$W/p6" | wc -l) entries" || echo "p6 target not created"
echo "-- documented recovery DATA_ASSET=football-db.tar.zst"; DATA_ASSET=football-db.tar.zst res "$W/p6-recover"; same "$W/export1" "$W/p6-recover"
mv "$FAKE_STORE/$bad" "$G3/ev/damaged-$bad"
echo "== C7 rolling asset listed but damaged (badrolling, newest)"; sleep 2
cp "$FAKE_STORE/football-db.tar.zst" "$G3/w/rolling.keep"; head -c 10 "$G3/w/rolling.keep" > "$FAKE_STORE/football-db.tar.zst"; lst
res "$W/c7" 2> "$G3/ev/c7.stderr"; tail -2 "$G3/ev/c7.stderr" | sed 's/^/   stderr: /'
h="$(grep -o 'DATA_ASSET=[^ ]*' "$G3/ev/c7.stderr" | tail -1)"; echo "hint: $h"; [ -e "$W/c7" ] && echo "c7 target exists" || echo "c7 target not created"
env "$h" bash "$SCRIPTS/data_restore.sh" "$W/c7-recover"; echo "exit $?"; same "$W/export1" "$W/c7-recover"
cp "$G3/w/rolling.keep" "$FAKE_STORE/football-db.tar.zst"
echo "== C8 negative: stray member refused by check_archive.sh"
mkdir -p "$W/stray"; cp "$W/export1/manifest.json" "$W/stray/"; echo x > "$W/stray/notes.txt"; tar --zstd -cf "$W/stray.tar.zst" -C "$W/stray" .
bash "$SCRIPTS/check_archive.sh" "$W/stray.tar.zst"; echo "exit $?"
echo "== C9 negative: publish refuses an export missing a declared table"
mkdir "$W/partial"; cp "$W/export1"/* "$W/partial/"; rm "$W/partial/players.parquet"; n0="$(ls "$FAKE_STORE" | wc -l)"
pub "$W/partial"; echo "assets before/after: $n0/$(ls "$FAKE_STORE" | wc -l)"
echo "== final"; lst; sha256sum "$FAKE_STORE"/*.tar.zst | sed "s#$G3#\$G3#"
