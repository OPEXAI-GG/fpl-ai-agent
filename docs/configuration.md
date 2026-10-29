# Configuration, secrets and live mode

Nothing below is needed to read the code, run the tests or run a dry plan from your own machine.
Settings come from environment variables locally and from **Settings -> Secrets and variables ->
Actions** in GitHub. No secret is stored in the repository or printed.

## Optional inputs (each degrades to a logged skip)

* `ODDS_API_KEY` - bookmaker prices are blended into the team goal rates.
* `ANTHROPIC_API_KEY` - the scout reads injury/news text and nudges availability (bounded, see [architecture.md](architecture.md)).
* `NOTIFY_WEBHOOK` (or a secret named `Discord_webhook`) - Discord/Slack-style webhook for the weekly report and failure alerts.

## Checklist

**To get the weekly dry-run reports in GitHub Actions:**

- [ ] `FPL_ENTRY_ID` (**variable**) - your team id: the number in `fantasy.premierleague.com/entry/<id>/event/<n>`.
- [ ] `NOTIFY_WEBHOOK` (**secret**) - a Discord or Slack incoming-webhook URL, so the report reaches you.

**Optional, each improves the plan:**

- [ ] `ANTHROPIC_API_KEY` (**secret**) - turns on the scout (a few cents a week).
- [ ] `ODDS_API_KEY` (**secret**) - The Odds API free tier (500 requests/month; the agent uses a handful).

**Only for live execution:**

- [ ] `FPL_REFRESH_TOKEN` (**secret**) - pasted once from your browser session.
- [ ] `FPL_TOKEN_URL` and `FPL_CLIENT_ID` (**variables**) - from the same browser session.
- [ ] `GH_SECRETS_PAT` (**secret**) - fine-grained token with *Secrets: read and write* on this repo, so the rotating refresh token can be stored back.
- [ ] `AGENT_MODE` (**variable**) - leave unset for dry, set to `live` to let the agent make the moves.
- [ ] `AGENT_ENABLED` (**variable**) - `false` stops everything.

Also in repository settings: branch protection requiring the `test` check, so a red CI blocks merges.

## Live mode (experimental)

One switch: the repository variable `AGENT_MODE`.

* Unset or `dry`: the agent only reports. Nothing logs in to FPL.
* `live`: the agent makes the moves on your team itself (previews, compares with the plan, confirms,
  sets the lineup, reads the team back) and the notification becomes a report of what it did.
* `AGENT_ENABLED=false` stops everything either way.

**Live execution has never been run against a real account.** It is tested against mock servers
only. The FPL sign-in endpoint and client id (`FPL_TOKEN_URL`, `FPL_CLIENT_ID`) must be copied from
your own browser session, and the write payloads follow FPL's documented shape but are unverified.
If you switch it on, check the first live run's read-back and the report. The Actions run summary
starts with a "settings" check that says whether live mode has everything it needs.

Rank mode: captaincy picks the player most likely to haul (10+ points) among those within 0.5
expected points of the best, and the report lists the top captain options. It cannot guarantee a
top rank.
