"""PostToolUse hook: ruff-format and ruff-fix an edited Python file, as CI's ruff gates expect.

Unused imports are not auto-removed (an import is often added one edit before its first use).
Remaining lint errors are fed back to Claude via exit 2; they never undo the edit.
"""

import json
import subprocess
import sys

path = json.load(sys.stdin).get("tool_input", {}).get("file_path", "")
if not path.endswith(".py"):
    sys.exit(0)

subprocess.run(["ruff", "format", "--quiet", path], check=False)
lint = subprocess.run(
    ["ruff", "check", "--fix", "--unfixable", "F401", "--quiet", path],
    capture_output=True,
    text=True,
    check=False,
)
if lint.returncode != 0:
    print(f"ruff found issues in {path}:\n{lint.stdout}{lint.stderr}", file=sys.stderr)
    sys.exit(2)
