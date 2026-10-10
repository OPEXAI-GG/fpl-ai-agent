#!/usr/bin/env bash
# Upload the newest plan in $1 (gw<N>.md / gw<N>.json) as latest.md / latest.json on the `plan`
# release, where the Discord bot reads it for /latest and /ask. Nothing to upload is fine: a run
# outside the deadline window writes no plan.
set -euo pipefail
dir="${1:-plan_out}"
md="$(ls -t "$dir"/gw*.md 2>/dev/null | head -1 || true)"
if [ -z "$md" ]; then echo "no plan written this run; nothing published"; exit 0; fi
tmp="$(mktemp -d)"
cp "$md" "$tmp/latest.md"
files=("$tmp/latest.md")
if [ -f "${md%.md}.json" ]; then cp "${md%.md}.json" "$tmp/latest.json"; files+=("$tmp/latest.json"); fi
gh release view plan >/dev/null 2>&1 || gh release create plan --title "latest plan" \
  --notes "The agent's newest weekly plan, read by the Discord bot. Replaced by every plan run."
gh release upload plan "${files[@]}" --clobber
echo "published $(basename "$md") as the latest plan"
