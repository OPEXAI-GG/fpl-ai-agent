. "$(dirname "$0")/env.sh"; set -euo pipefail
uname -s; command -v zstd tar bash; tar --version | head -1
r="$(echo hello-g3 | zstd | zstd -d)"; echo "zstd roundtrip: $r"
