"""PreToolUse hook: refuse edits to secrets and the lockfile (exit 2 blocks the tool call)."""

import json
import re
import sys

BLOCKED = [
    (re.compile(r"(^|[\\/])\.env(\..+)?$"), "secrets live in .env files - never edit them"),
    (re.compile(r"\.token$"), "token files hold credentials - never edit them"),
    (re.compile(r"(^|[\\/])uv\.lock$"), "change dependencies with `uv add` / `uv lock`"),
    (re.compile(r"(^|[\\/])package-lock\.json$"), "change dependencies with `npm install`"),
]

path = json.load(sys.stdin).get("tool_input", {}).get("file_path", "")
for pattern, reason in BLOCKED:
    if pattern.search(path):
        print(f"Blocked edit to {path}: {reason}.", file=sys.stderr)
        sys.exit(2)
