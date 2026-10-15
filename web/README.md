# FPL Sim website

The paid website for the FPL agent: people enter their FPL manager ID and get advice (never
autopilot, never their FPL password). Free: this week's captain and vice. Pro (monthly or a season
pass, through Stripe): the full plan from the engine's `decide()`.

```
Cloudflare Worker (web/src) + D1 database (web/migrations)
   ^  pages, sign-in by emailed link, Stripe checkout/portal/webhook
   |
   |  /api/engine/*  (Bearer ENGINE_TOKEN)
   |
GitHub Actions site.yml -> fpl-agent site-sync (src/fpl_agent/site.py)
   shared projection once per run, then a free pick or full plan per manager id
```

* `site-deploy.yml` deploys on every change under `web/` that reaches master (or by hand): creates
  the D1 database, applies migrations, deploys, sets the secrets and sets up Stripe
  (`scripts/stripe-setup.mjs`: product, prices by lookup key, customer portal, webhook).
* `site.yml` runs every 3 hours and plans from 72 hours to 45 minutes before each deadline. A full
  plan is kept for 8 hours, except in the last 5 hours, when every run re-plans after team news.
  It only reads public FPL data and never publishes the football database, so your own weekly
  run is untouched.
* The address is https://fplsim.com once that domain is in the Cloudflare account, else the
  Worker's workers.dev address (`scripts/site_env.sh`).

## Settings

| Name | Where | What |
|---|---|---|
| `CLOUDFLARE_API_TOKEN` | secret (exists) | needs Workers Scripts edit and D1 edit |
| `CLOUDFLARE_ACCOUNT_ID` | variable (exists) | |
| `STRIPE_SECRET_KEY` | secret | `sk_test_...` to try it, `sk_live_...` to take money |
| `RESEND_API_KEY` | secret | sends the sign-in emails; verify fplsim.com in Resend |
| `ENGINE_TOKEN` | secret, optional | otherwise derived from the Cloudflare token |
| `DISCORD_BOT_GITHUB_TOKEN` | secret (exists), optional | lets a payment start a plan run at once |

Prices, the season end and VAT are in `wrangler.toml` (`PRICE_*`, `SEASON_END`, `STRIPE_TAX`);
the amounts charged are set once by the Stripe setup (`MONTHLY_PENCE`, `SEASON_PENCE`). Turn
`STRIPE_TAX` to `on` after adding your address in Stripe Tax.

## Local

```bash
cd web && npm ci && npm test
node --no-warnings scripts/preview.mjs   # http://localhost:8787 with sample plans
```
