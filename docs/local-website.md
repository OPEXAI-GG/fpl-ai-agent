# Matchplan on your own computer

For one manager to use every gameweek before the website is hosted for subscribers. It is the
same read-only site as [the demo](website.md): it reads public FPL data, never signs in to FPL and
never changes a team.

## Windows: one double-click

1. Copy [`scripts/windows/Matchplan.cmd`](../scripts/windows/Matchplan.cmd) to your Desktop.
   `ENTRY` at the top is the team it opens on (default 8700026); edit it with Notepad to change it.
2. Double-click it. On the first run it offers to install what is missing (Git and Node.js 22+
   with `winget`, `uv` from astral.sh; each asks Y/N first), downloads the program to
   `%LOCALAPPDATA%\Matchplan\fpl-ai-agent`, then downloads about 200 MB of football data. The
   first start takes roughly 5 to 10 minutes; later starts take about a minute.
3. The browser opens on your team. Keep the black window open while you use the site; close it to
   stop everything.

Disk use is about 4 GB (the database and one published copy of it).

## Any OS: one command

```bash
uv run fpl-agent web local --entry 8700026
```

Options: `--no-refresh` (skip the data check), `--force-refresh` (download again), `--no-browser`,
`--workers N` (default 1), `--port` (API, 8000) and `--site-port` (site, 3000).

## What each start does

1. **Data.** Asks GitHub for the `data` release asset (public, no sign-in). CI refreshes it every
   morning (prices, availability, fixtures) and in full every Tuesday after scores lock. If it is
   newer than the last import, it is downloaded, size-checked, unpacked and imported into a new
   file, which replaces the old database only once it has an FPL snapshot. Offline, the database
   you have is used and the window says so.
2. **Revision.** A new read-only revision is published (with FPL's live bootstrap, fixtures and
   the top-10k ownership sample) when the data changed, when there is none, or when the deadline
   it was captured for has passed. Older revisions are deleted.
3. **Site.** `npm ci` and `next build` run only when the lockfile or site source changed.
4. **Start.** The API, one worker and the site start on `127.0.0.1` only. Your team's plan is
   queued straight away, so the Plan tab is usually filled in by the time you open it. A plan
   may run for up to 40 minutes on a single machine (`WEB_PLAN_BUDGET_S=2400`); the preliminary
   answer for your most likely free-transfer count arrives in about two minutes.

A second double-click while it is running just opens the browser.

## Weekly routine

Start Matchplan after the gameweek's last match (Tuesday's full data refresh is best) and again on
deadline day for the latest prices and availability. Read the plan for **your** free-transfer
count in the table: the public data can't see your exact count or transfers you made since the
last deadline, so plan before you make this week's moves. Make any changes yourself on the FPL
site.

## Limits

- The data is as fresh as the last CI refresh (daily). Late team news after that is not in it.
- One computer, one person: the store is SQLite and the workers are local processes.
- Until the pull request is merged the launcher follows its branch; after the merge it falls back
  to `master` automatically.
