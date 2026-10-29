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
- `TASK.md` (in the candidate) contains the owner's FPL entry ID `<owner entry ID>` at L2145 and L2157.
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

---

# Re-check of Sami's actual commits (branch `sami/faa-43-publish-db-hardening`, HEAD `450e9fe`), 2026-10-29

Commits on top of `c6a0c5f`:
- `7505aa5`: ignore rules and attributes;
- `dc1e325`: 37 source, test and script files;
- `1e2e3cd`: 10 docs;
- `450e9fe`: 360 evidence files.

I could see no `origin/sami/…` ref locally, so I could not confirm the push.

## Verdict: CHANGES NEEDED (two items) before the branch is treated as published and reviewed

### Passed

- **C2 (modes):** closed. `check_archive.sh`, `data_publish.sh`, `data_restore.sh` and `plan_publish.sh` are
  100755 at HEAD.
- **Content matches the 47-file candidate.**
  - 24 blobs match the manifest hashes exactly.
  - The other 23 differ **only** in line endings: the working copies are CRLF and the blobs are LF, after
    `core.autocrlf` normalisation. No blob contains CRLF. Script blobs equal the Gate 0 r3 approved hashes.
- **C1 (staging):** closed for new material.
  - The new ignore rules cover `hive/`, `palace*`, `roster*`, `restore-gate.json`, `research/hires/`, archives and
    temp/copied trees. No new parquet, duckdb or zst file was added.
  - The 360 evidence files are md/json/log/png/csv/patch and small scripts.
  - `.gitattributes` sets `*.sh eol=lf`; `*.py` is not covered, which is fine given the normalisation above.
  - `ruff` now excludes `artifacts/`.
- **Secrets:** none in `git diff c6a0c5f 450e9fe`. I scanned for GitHub, AWS, Slack, Anthropic and OpenAI keys,
  private keys, webhooks, bearer tokens, FPL cookies and the broker token. `download.cjs` reads the token from an
  environment variable; there's no literal.
- **Authors:** the commit author email is the same one used on earlier public commits, so nothing new is exposed.

### Required

**X1. Redact the owner's FPL identity from the current tree (god's decision: a new commit, no history rewrite).**
At HEAD, 13 tracked files contain entry ID `<owner entry ID>`:
- `FPL Input Flow Audit (Copy).md`;
- `TASK.md`;
- `artifacts/codex-db-review-r2-604dd0fef9/coverage.csv`;
- `artifacts/db-audit-20261022/coverage.csv`;
- `artifacts/db-hardening-20261023T143050Z/lint-smoke.log`;
- `artifacts/deadline-20261023/{c03_no_lock_diag.py, check_refresh.py, inputs/entry.json, inputs/transfers.json}`;
- `artifacts/dry-run-verification.json`;
- `artifacts/gw8-analysis.md`;
- `artifacts/gw8-injury-aware-analysis.md`;
- my own `artifacts/wegz-repo-scope-review-20261029.md`. I have already redacted my own file on disk. Its new
  SHA-256 is printed in the outbox message; recommit that version.

`artifacts/deadline-20261023/inputs/entry.json` holds more than the ID: the owner's **first and last name**,
region, join date and league ranks. `transfers.json` holds the team's transfer history.
- Fix: remove those two from the index with `git rm --cached` (keep the local copies, add an ignore rule).
- Replace the ID with a placeholder in the other text files.
- In the commit message and PR, say honestly that the ID and name stay in public history at `c6a0c5f` and
  earlier, and that removing them from history needs a separate human decision.

**X2. Drop the excerpt of the Munder Difflin app.**
- `artifacts/FAA-7-production/broker-header-inspection.txt` is copied source from
  `C:/Program Files/Munder Difflin/resources/app.asar`: how the broker builds GitHub and auth headers.
  `inspect-broker.cjs` is the script that extracted it.
- It's third-party, non-project code, now in a public repository, and it describes how credentials are injected.
- Remove both from the commit, or `git rm --cached` them. Keep the local copies for evidence.

### Recommended (not blocking)

- **R1.** `artifacts/windows-sandbox-fix/` (Codex sandbox configuration for this PC) is local-machine tooling, not
  project evidence. Consider leaving it out.
- **R2.** 26 added lines contain `C:/Users/<account>` and 5 contain `/c/Users/<account>`, all local paths.
  The privacy risk is low; consider normalising them over time.
- **R3.** 368 parquet export files (in 12 folders, for example `db-hardening-20261023T143050Z/*/export`,
  `db-migration-rehearsal-*/export`, `deadline-20261023/work-export`) were **already tracked** before this branch.
  The new `*.parquet` rule doesn't untrack them.
  - Optional index-only cleanup: `git rm -r --cached` on them. They stay in history and on disk.
  - Check they don't hold owner-specific data before deciding.

## Gates after X1-X2

| Gate | Status |
|---|---|
| Publishing the source branch and opening a PR | Acceptable once X1-X2 are in a new commit. I re-check that SHA. |
| Merge to master | **Not approved.** Several source files are not reviewed by me (see the table above). |
| Linux CI with zstd (15 archive cases) | Not run; required before merge |
| Production (migration/rollback rehearsal, prospective export with deadlines and source attempts, link apply) | Unverified; no production writes |
