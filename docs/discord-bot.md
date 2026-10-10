# Talking to the agent on Discord

Once set up, you type slash commands to the bot in your server or in a DM with it:

| Command | What happens | How fast |
|---|---|---|
| `/ask question:` | Answers in plain words from the latest plan ("who's captain and why?", "should I use the Bench Boost?") | a few seconds |
| `/latest` | Shows the latest plan report | instant |
| `/status` | Dry or live, and how the last plan, data and review runs went | instant |
| `/plan` (`force:`, `real:` optional) | Runs the weekly plan now as a **dry run** and posts the report back to you: nothing signs in to FPL and your team is not touched. It plans whenever you ask, even days before the deadline (`force:False` keeps the usual 26-hour window). `real:True` makes it a real run instead, which keeps the deadline window and **makes the moves on your team only when the agent is in live mode**, exactly like pressing Run workflow. | a few minutes |
| `/explain players:` | Why players project as they do this gameweek (fixture, minutes, points by source) | a few minutes |

Only the Discord accounts you list as owners can use it; anyone else gets a polite refusal and nothing runs.

## How it works

Discord sends each command to a small program on Cloudflare's free Workers plan (`discord/`), which is always on, so the bot never "goes offline". It checks Discord's signature and that the sender is you, answers at once with "thinking...", then edits that message with the result:

* `/latest`, `/ask` and `/status` read the newest plan, which every plan run now publishes to the `plan` release, and the run history. `/ask` sends that plan to Claude with your question.
* `/plan` and `/explain` start a GitHub Actions run (`plan.yml`, `discord.yml`), which posts the answer back into the same conversation when it finishes.

The bot cannot read ordinary chat messages: you talk to it through slash commands (`/ask` for anything in plain words).

## Setting it up (about 20 minutes, once)

Never paste any of these values into the chat or into a file in the repository. Paste them only into the pages named below.

### A. Create the Discord app

1. Open <https://discord.com/developers/applications> and click **New Application**. Name it (for example `FPL Agent`) and create it.
2. On **General Information**, copy the **Application ID** and the **Public Key**. Keep the tab open; you come back in step E.
3. Open **Bot**, click **Reset Token**, and copy the token (shown once).
4. Open **Installation**. Under Installation Contexts tick **User Install** and **Guild Install**. Under Install Link choose **Discord Provided Link**. Under Default Install Settings, for both, the scope `applications.commands` is enough.
5. Your own Discord user ID: in the Discord app, **User Settings**, **Advanced**, turn on **Developer Mode**. Then right-click your name anywhere and choose **Copy User ID**.

### B. Create a free Cloudflare account

1. Sign up at <https://dash.cloudflare.com/sign-up> (free plan).
2. Click **Workers & Pages** in the left menu once. If it asks you to choose a `workers.dev` subdomain, pick any name.
3. On that page, copy your **Account ID** (right-hand side).
4. Click your profile icon, **My Profile**, **API Tokens**, **Create Token**, use the template **Edit Cloudflare Workers**, choose your account under Account Resources and **All zones** under Zone Resources, then **Continue to summary**, **Create Token**, and copy it.

### C. A GitHub token for the bot

GitHub, your picture, **Settings**, **Developer settings**, **Personal access tokens**, **Fine-grained tokens**, **Generate new token**:

* Name `fpl-agent-discord`, expiration 1 year, resource owner **opexai-gg**, **Only select repositories**: `fpl-ai-agent`.
* Repository permissions: **Actions: Read and write**, **Contents: Read-only**, **Variables: Read-only**. Everything else stays "No access".
* Generate and copy it.

### D. Put the values into the repository

Repository **Settings**, **Secrets and variables**, **Actions**.

Secrets tab (**New repository secret** for each):

| Name | Value |
|---|---|
| `DISCORD_BOT_TOKEN` | the bot token from A3 |
| `CLOUDFLARE_API_TOKEN` | the token from B4 |
| `DISCORD_BOT_GITHUB_TOKEN` | the token from C |
| `ANTHROPIC_API_KEY` | your Claude API key, if it is not there already (needed for `/ask`) |

Variables tab (**New repository variable** for each):

| Name | Value |
|---|---|
| `DISCORD_APP_ID` | the Application ID from A2 |
| `DISCORD_PUBLIC_KEY` | the Public Key from A2 |
| `DISCORD_OWNER_IDS` | your user ID from A5 (several: comma separated) |
| `CLOUDFLARE_ACCOUNT_ID` | the Account ID from B3 |

### E. Switch it on

1. **Actions**, **discord-deploy**, **Run workflow**. When it finishes, its summary shows a line "Interactions Endpoint URL ...: https://fpl-agent-discord.<your-subdomain>.workers.dev".
2. Back on the Discord developer page, **General Information**, paste that address into **Interactions Endpoint URL** and click **Save Changes**. Discord tests it straight away; if it saves, the bot is live.
3. **Installation**, copy the install link and open it. Choose **Add to My Apps** to use it in DMs anywhere, and/or **Add to Server** for your server.
4. Type `/status` to the bot. Then `/latest`; it shows a plan after the first plan run that includes this change.

## If something is off

* **"The application did not respond" / "This interaction failed"**: the endpoint address in step E2 is missing or wrong, or the last deploy failed. Re-run **discord-deploy** and check its log.
* **Changed a variable or secret above?** Re-run **discord-deploy**: the bot keeps a copy of them from the last deploy.
* **`/plan` says another job is running**: the data or plan job is using the database; a plan queued behind it would be skipped for login-token safety. Try again a few minutes later.
* **`/plan` finished but no report came back**: Discord lets a command's reply arrive for 15 minutes. A longer run still posts to your usual alerts channel on its first plan of the gameweek, and `/latest` shows it.
* **`/status` says "unknown"** for the mode: the GitHub token in C is missing **Variables: Read-only**.
