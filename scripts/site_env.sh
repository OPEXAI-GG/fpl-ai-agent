#!/usr/bin/env bash
# Works out the website's address and the engine's token, and appends them to $GITHUB_ENV:
#   SITE_URL      https://fplsim.com once that domain is in the Cloudflare account, else the
#                 Worker's own workers.dev address
#   SITE_ZONE     the domain when it is in the account (the deploy then attaches it), else empty
#   ENGINE_TOKEN  the ENGINE_TOKEN secret if set, else derived from the Cloudflare token, so the
#                 website and the engine share it without another secret to create
# Needs CLOUDFLARE_API_TOKEN and CLOUDFLARE_ACCOUNT_ID. SITE_DOMAIN overrides the domain.
set -euo pipefail
domain="${SITE_DOMAIN:-fplsim.com}"
api="https://api.cloudflare.com/client/v4"
auth=(-H "Authorization: Bearer ${CLOUDFLARE_API_TOKEN}")
zone=$(curl -sS "${auth[@]}" "$api/zones?name=$domain&account.id=$CLOUDFLARE_ACCOUNT_ID" \
  | jq -r '.result[0].id // empty' 2>/dev/null || true)
if [ -n "$zone" ]; then
  url="https://$domain"
else
  sub=$(curl -sS "${auth[@]}" "$api/accounts/$CLOUDFLARE_ACCOUNT_ID/workers/subdomain" \
    | jq -r '.result.subdomain // empty' 2>/dev/null || true)
  url="${sub:+https://fplsim.$sub.workers.dev}"
  domain=""
fi
token="${ENGINE_TOKEN:-}"
if [ -z "$token" ]; then
  token=$(printf 'fplsim-engine:%s' "$CLOUDFLARE_API_TOKEN" | sha256sum | cut -c1-64)
fi
echo "::add-mask::$token"
{
  echo "SITE_URL=$url"
  echo "SITE_ZONE=$domain"
  echo "ENGINE_TOKEN=$token"
} >> "${GITHUB_ENV:-/dev/stdout}"
echo "website address: ${url:-unknown (no workers.dev subdomain yet)}"
