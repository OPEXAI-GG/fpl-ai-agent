# Repository scope review for Sami's push (FAA-43)

Reviewer: Wegz (wegz-mvsc3fox), 2026-10-29. Executor: Sami (sami-mvtqte55).

Reviewed:
- `artifacts/final-db-review-20261029/candidate-files.json`, SHA-256
  `11c603364898a9e95ce6850b23d73a09a257f1c2950e02aeed6989676a416364` (47 files);
- `README.md` in the same folder, SHA-256 `0a23737300646e1333f917a902d04af2a8043c4b3594db14f3e739317aff6b47`.

Sami has not yet published a staged list or commit, so this review covers the prepared candidate. When Sami has a
commit, I'll re-check it: `git show --stat`, the tree hashes and the file modes.

## Verdict: CHANGES NEEDED before the push (three conditions on how it is staged and committed); content otherwise acceptable

The 47 files are suitable for a **source push to `task/phase1-dashboard`** once C1-C3 are met. This is **not**
approval to merge into master, deploy, or run the production data gates.

## Checks (read-only; no tests re-run, no Git writes)

- **The candidate matches disk.** All 47 files exist and their SHA-256 equals the manifest.
- **No unpushed commits.** HEAD `c6a0c5f` equals `origin/task/phase1-dashboard`, and `origin/master` is
  `51f0def`. All candidate work is uncommitted: 24 modified tracked files (+2084 −174, artifacts excluded) plus
  new modules and tests.
- **Nothing is missing.** Every changed or untracked path outside `artifacts/` is either in the candidate, or is
  runtime or organisational (listed under C1 and N2).
- **Scripts match the reviewed revision.** `check_archive.sh` (b7a31027), `data_publish.sh` (3821d5a6) and
  `data_restore.sh` (f8835a21) are byte-identical to the Gate 0 r3 tree I approved (patch `73751d56…f6a1`).
- **Secrets.** I scanned the 47 files plus `research/` and `tasks/plan.md` for GitHub, AWS, Slack, Anthropic and
  OpenAI tokens, private keys, Discord webhooks, bearer tokens and FPL cookie names. **None found.** The only email
  is a package name (`shadcn@1.0.0`).
- **Tests.** The 894-passed / 92.95% evidence comes from `full-tests.log` and was not re-run. The 15 Linux archive
  cases have still not run.

## Conditions

**C1. Stage files explicitly, and add ignore rules first.**
- `.gitignore` doesn't cover any of these runtime or private paths:
  - `hive/` (agent inboxes and memory);
  - `palace/`, `palace.lock`, `palace.rebuilt-*` (the MemPalace vector database);
  - `roster.json`, `roster-backups/`, `restore-gate.json`;
  - `artifacts/`, which is 6.8 GB. It holds production parquet exports (`FAA-7-production/r20261028/export`), many
    pytest temp and export folders, and copied review repositories. 689 artifact files are already tracked.
- A `git add -A` or `git add .` would publish agent memory and production data.
- Fix: add those paths to `.gitignore` (or have Sami set the rule), then stage the 47 paths by name.
- Any artifact evidence should be added only as named files (this README, `candidate-files.json`, the review
  `.md` files), never as whole folders.

**C2. Executable modes.**
- `scripts/check_archive.sh` is untracked, and this checkout has `core.filemode=false`.
- Add it with `git update-index --add --chmod=+x scripts/check_archive.sh`, or apply the r3 patch with
  `git apply --index`.
- Before committing, `git ls-files -s scripts/` must show **100755** for `check_archive.sh`, `data_publish.sh`
  and `data_restore.sh`.

**C3. Personal data: an FPL entry ID.**
- `TASK.md` (in the candidate) contains the owner's FPL entry ID `8700026` at L2145 and L2157.
- It is **already** on `origin/task/phase1-dashboard` in five files, including `TASK.md`,
  `FPL Input Flow Audit (Copy).md` and two `coverage.csv` artifacts.
- Before pushing, Sami must confirm the repository is private. Then one of:
  - (a) the owner explicitly accepts the ID in the repo; or
  - (b) redact it from `TASK.md` and from the files already tracked, in a new commit.
- Removing it from history needs a separate human decision. Don't rewrite history without one.

## Recommended (not blocking)

- **N1.** `core.autocrlf=true` and there's no `.gitattributes`. Add `*.sh text eol=lf` (and `*.py text eol=lf`)
  so a Windows commit can never give Linux CI a script with CRLF line endings.
- **N2.** `research/hires/*.json`, `research/github-owner-first-assignment.md` and `tasks/plan.md` are not in the
  candidate. They're company or organisational files, not source; decide whether to include or ignore them.
  - `tasks/plan.md` L27 contains a local Windows path with the user's account name.
  - The secret scan of `research/hires/*.json` found nothing.
- **N3.** Keep `TASK.md` (287 KB, a working log) out of the source tree's root in the reorganisation, or move it
  to `docs/history/`. Its size and history make it a recurring privacy risk (see C3).

## What is approved, and what is not

| Gate | Status |
|---|---|
| Source push of the 47-file candidate to `task/phase1-dashboard` | Acceptable once C1-C3 are met. I re-check the actual commit. |
| My earlier code reviews covering these files | Export scripts (Gate 0 r3), M02 `players.py` / `features.py` (switch kept off; W1/W2 open) |
| Merge to master or release | **Not approved.** Not reviewed by me: `pipeline.py` provenance and repair integration, `schema.py` provenance tables (FAA-41), `quality.py`, `agent.py`, `execution/safety.py`, `health.py`, backtest and replay `--validated`, `player_links` apply. Safety-rail files need review first. |
| Linux CI with zstd (15 archive cases) | Not run |
| Production: migration and rollback rehearsal, verified prospective export (deadlines, source attempts), link apply | Not verified; no production writes are authorised |
