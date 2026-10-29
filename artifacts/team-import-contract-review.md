# Review: team import API v0 contract (FAA-29)

Reviewer: Wegz (wegz-mvsc3fox, Claude). 2026-10-28. Contract review against current code; no code changes, no tests run.
Reviewed file: `docs/design/team-import-api-v0.md`, 213 lines,
SHA-256 `3a1e231bf740365a588b8503084a2bb114eec690a4ba77103a0e2831eb45bf93`.
Code read (working tree on `task/phase1-dashboard`, uncommitted changes included): `agent.py:222-241`,
`data/entry.py:66-110`, `data/fpl_client.py:18-66`, `dashboard/server.py`, `dashboard/service.py:28,153-201,322`,
`db/schema.py:63-65`, `db/database.py:533-539`, `rules/engine.py:31-39`.

## Verdict: REQUEST CHANGES

The contract has good bones: read-only, POST behind the existing Origin and token guard, strict input checks,
same-season ID mapping, errors instead of guesses, and an `exploratory` label. Nine revisions are needed before
implementation, because several promises do not match what the code can deliver.

## Required revisions

**C1. Season is not in the bootstrap (§5.1, L56, L116).** The FPL bootstrap has no season field. Today the code
hardcodes `season: int = 2026` (`agent.py:42`, `pipeline.py:53`). Also, `Rules.nearest()` (`rules/engine.py:31-39`)
silently uses the closest rules file when the exact season has none.
Revise: define the derivation exactly, from bootstrap data only. For example: season = year of the GW1
`deadline_time` in `events`, with the bootstrap `is_next` event inside that season. Do not use the system clock,
the config default or fixture calendars. Fail with `409 season_rollover` or `502 upstream_contract` if it can't be
derived. Require an exact rules file for that season, or return a warning naming the rules season used.

**C2. Free Hit and pre/post-transfer meaning (§4, §5.2, §5.5).**
- The response mixes timelines. Squad, `is_captain`, `multiplier`, `bank`, `value` and `active_chip` come from
  `source_gw` picks (`entry.py:105-110`). `now_cost` and selling prices use live prices. Transfers for `next_gw`
  are counted but not applied, and free transfers are not reduced by them.
  Revise: tag every state field with its basis (`source_gw` or `live`). Say that captaincy and multiplier are last
  week's. Define `active_chip` as the chip played in `source_gw`; a pending chip for `next_gw` isn't public. Say
  that bank and free transfers are the state before any `already_transferred` moves.
- After a Free Hit, `bank` and `value` come from the `next_gw-2` picks (`agent.py:232-235`). State that, and test
  that the reverted bank is used.
- Add test cases: a Free Hit at `next_gw-1` when `next_gw-1 == 1` (the agent rule stops at `last > 1`), and a
  Wildcard at `next_gw-1`, which should not trigger a revert.

**C3. Purchase and selling prices are reconstructed, not exact (§6, test 1).** `parse_squad` takes the latest
transfer-in cost. Otherwise it uses `now_cost - cost_change_start` (the season opening price) (`entry.py:85-91`).
That is wrong for a manager who joined after GW1, because their first squad was bought at that week's prices. It is
also not checked against FPL. Test 1, "prices equal `selling_price()` output", proves nothing, because it compares
the function with itself.
Revise:
- add `price_basis: "reconstructed"` and say exact values need the signed-in endpoint, which is out of scope;
- warn when the entry's first history event is after GW1;
- add a consistency check: the sum of `now_cost` at `source_gw` plus `bank` against `entry_history.value`, warning
  on mismatch;
- test with hand-computed expected prices, including a late joiner and a player sold and bought back.

**C4. Projection provenance (§7).** "A run exists for (season, next_gw)" plus newest `created_at` is not enough.
The `projections` table has a `model` column (`schema.py:63-65`), so baseline and candidate runs (M02) can sit
side by side. The snapshot staleness check measures the database's newest snapshot against now, not the input the
run used.
Revise:
- choose the run as the newest one for `(season, next_gw)` with `created_at` before the `next_gw` deadline, filtered
  to a declared model (the baseline until god accepts a candidate);
- return `model` and `run_id`;
- map IDs with `fpl_players_as_of(season, gw, run.created_at)` (`database.py:533`), so the mapping matches what the
  run saw;
- if the run's input snapshot can't be identified, state that limit in `reason` instead of implying alignment;
- take the deadline from bootstrap `events`, not `approx_deadline`.

**C5. All-null contradicts per-player null (§7, L140-141 vs L150; test 5).** The introduction says any failed
check nulls every player's projection. The last row instead nulls only unmatched players and sets `misaligned`.
Revise: pick one. Recommended: when 1 to 14 picks match, return `status: "partial"` with the matched players'
projections kept and `unmatched_fpl_ids` listed. Keep `misaligned` only for season, gameweek or deadline failures,
which null everything. Also require exactly one `player_id` per `(season, fpl_id)`: two or more counts as
unmatched, never "first row wins". Test both the partial case and the all-null case.

**C6. Cache key and revalidation (§3 L57-58, test 12).** The key `(entry_id, next_gw)` lacks the season. If the
cache is consulted before the bootstrap fetch, it can serve a squad after a deadline has passed.
`db_snapshot_age_hours`, `stale` and the projection status are frozen at first fetch.
Revise:
- key `(season, entry_id, next_gw)`;
- expiry is the earlier of 10 minutes and the `next_gw` deadline;
- on a cache hit, recompute freshness and projection status from the database, and set `cached_at` and the age;
- cap the cache size (for example, 64 entries, oldest evicted first);
- add tests for expiry at the deadline, a season change and recomputed staleness.

**C7. The busy guard must be atomic, and fetch time must be bounded (§3 L57, §8 409/503).** The dashboard
server is multi-threaded (`server.py:210`, `ThreadingMixIn`), so two imports can run at the same time. A check-then-set
flag would race. `FPLClient` defaults are a 30 s timeout with 4 retries and up to 30 s waits (`fpl_client.py:22-27,52-66`).
In the worst case that is roughly 4.5 minutes per fetch, and an import makes up to four fetches, all while holding
the guard.
Revise:
- use a `threading.Lock` taken without waiting before any FPL call and released in `finally`;
- decide whether imports are also refused while a dashboard job is running (`jobs.busy`);
- inject a client with short timeouts (for example 5-10 s) and at most one retry;
- add a total time budget for the import (for example 20-30 s); over budget gives `503 upstream_unavailable`;
- test two concurrent imports (exactly one gets `409 busy`, using a blocking fake client) and test that the
  budget is enforced.

**C8. The entry ID identifies a person (§4 L108-109, §8).** Leaving out names doesn't make the payload
anonymous. An FPL entry ID publicly resolves to the manager's name, and squad history is linkable.
Revise:
- treat `entry_id` as personal data;
- keep it out of logs and error text: `fpl_client` errors embed the URL with the ID (`fpl_client.py:50`), so
  `_safe_reason` and the 500 log must redact it;
- keep the cache in memory only and clear it on shutdown (already implied);
- replace "avoids handling personal data" with "minimises personal data; hosted use needs a privacy review";
- test that no error body or log line contains the entry ID.

**C9. Error mapping and ownership details.**
- A 404 from the picks endpoint for a gameweek before the entry joined should give `409 no_squad_yet`, not
  `404 entry_not_found`. Decide this and test it.
- §9 still says contracts.py ownership is open. Per god (2026-10-28), Pablo owns the entry-specific validators in
  `contracts.py` and Nabil is notified. Update the table.
- Test 15 runs pytest with `--basetemp` inside the repo. On this machine that OneDrive folder causes
  `PermissionError`s that aren't product failures. Use a scratch folder outside OneDrive.

## Tests: covered vs missing

Covered well (tests 3, 4, 8-11, 13, 14): no squad yet, already-made transfers, bad shapes, upstream errors, input
validation with zero FPL calls, guards, no local-user data, and the read model staying offline.

Still missing, mapped to the revisions above:
- C1: season derived from bootstrap; missing season rules file.
- C2: Free Hit edge cases; Free Hit bank revert.
- C3: hand-computed prices; late joiner.
- C4: mixed models; run after deadline; mapping as of the run.
- C5: partial vs all-null.
- C6: cache across the deadline and season; refreshed staleness.
- C7: concurrent busy; time budget.
- C8: entry ID redaction.
- C9: picks 404 before the entry joined.

## Not in scope here

Model quality stays `exploratory` (FAA-15 pending). Hosted multi-user isolation, rate limiting and abuse controls
are correctly excluded (§11). They need their own contract and review.

## Re-review

I'll re-review only the revised sections against C1-C9 and record the new SHA-256. Code review follows separately
at implementation handoff.

---

# Re-review r2 (2026-10-28)

Reviewed revision 2: `docs/design/team-import-api-v0.md`, 265 lines,
SHA-256 `1b92dbea084728baf7cb3d599b1dc814109ee2849119261e83a0a078be25f527`. Assessed against C1-C9 above; the old
text is superseded.

## Verdict: REQUEST CHANGES (narrowed)

| Item | r2 status | Evidence / what remains |
|---|---|---|
| C1 season | **Resolved** | §2.4, §5.1: season comes from config and is honestly labelled. `409 season_mismatch` if the DB's newest season or the bootstrap first deadline (July-December) disagree. No clock guess. `rules_file`/`rules_exact` plus a warning (§6). Tests 17. |
| C2 timelines | **Open** | Only `prices_source` was added. Still needed: say captain, multiplier, bank, value and `active_chip` are `source_gw` state; say bank and free transfers are before `already_transferred` moves; say the bank reverts after a Free Hit; tests for a Free Hit at GW1 (no revert) and a Wildcard at `next_gw-1` (no revert). |
| C3 prices | **Mostly resolved** | §6 L157-166, L245-246: estimate label, `purchase_price_source`, arithmetic test, no claim of matching FPL. Remaining: `"season start"` is also wrong for a manager who joined after GW1. Add a warning when the entry's first history event is after GW1, plus a test. |
| C4 provenance | **Partly resolved** | One run, newest before the deadline, `run_id` and `db_snapshot_at` returned (L188-191, test 18). Remaining: filter by `model` (the `projections.model` column; a baseline and an M02 candidate run for the same GW would otherwise be picked by recency) and return it. Name which snapshot maps IDs: use `fpl_players_as_of(season, gw, run.created_at)`. |
| C5 null rule | **Open** | L176-177 still says any failure nulls all players, but L186 nulls only unmatched players. Choose one (recommended: `partial` status) and treat duplicate mappings as unmatched. Test both. |
| C6 cache | **Mostly resolved** | L66-74: key includes season and deadline, expiry at the earlier of 10 minutes or the deadline, flush on a new bootstrap, no error caching, test 12. Remaining: say that a cache hit recomputes `freshness`/`stale` from the database (or reports `cached_at`), and cap the entry count. |
| C7 lock/timeouts | **Open** | Unchanged. The server is threaded (`server.py:210`). Needed: an atomic non-blocking lock released in `finally`, a decision on `jobs.busy`, an injected client with short timeouts and at most one retry, a total import budget leading to 503, and tests for concurrent busy and the budget. |
| C8 personal data | **Open** | L128-129 unchanged. Treat `entry_id` as personal data. Redact it from error messages and logs (`fpl_client.py:50` puts the URL with the ID in the exception). Reword "avoids handling personal data". Add a redaction test. |
| C9 errors/ownership | **Partly resolved** | §9 ownership fixed. Remaining: picks 404 for a GW before the entry joined should be `no_squad_yet`; test 15 still uses `--basetemp` inside the repo (OneDrive `PermissionError`s here). |

Cosmetic: tests are numbered 1-12, 16-18, 13-15. Renumber.

Re-review r3 will cover only C2, C3 (late joiner), C4 (model and mapping), C5, C6 (remaining), C7, C8 and C9.

**Explicit verdicts for revision 2 (`1b92dbea…f527`), as god asked:**
- Season: **accepted as written.** The configured season, checked against the DB's newest season and the bootstrap
  first-deadline window (§5.1), with `rules_exact` reported, is accepted instead of deriving the season from the
  bootstrap. My r1 demand to derive the season is **withdrawn**; nothing further is needed for C1.
- Server concurrency: the dashboard server is threaded (`server.py:210`), so requests are **not** sequential. The
  `409 busy` rule therefore only works with an atomic lock. That is the whole of C7's lock item, together with the
  timeout and time-budget items listed above.
- The r1 findings above stay as history. The r2 table above is the current status.

---

# Re-review r3: changed sections (2026-10-29)

Reviewed revision 3: `docs/design/team-import-api-v0.md`,
SHA-256 `e023ee25e48a268cd098885308c73172c2b8ada235acdddfebb2a0c067baaf04`. Checked against the r2 open items, and
against `fpl_client.py:28-50` for the time-limit claim.

## Verdict: APPROVE for implementation, with two one-line corrections

The corrections can be checked at code review; no further contract round is needed.

| Item | r3 status | Evidence |
|---|---|---|
| C2 timelines | **Resolved** | §5.2 per-field timeline table and `state.basis`. Captaincy is last week's. `active_chip` is the `source_gw` chip only. Bank and free transfers are before pending moves. Free Hit bank revert. GW1 Free Hit and Wildcard/Bench Boost cases. Tests 2-6. |
| C3 late joiner | **Resolved** | `late_joiner_price_estimate` warning naming the "opening price" players. Prices hand-computed, not via `selling_price` (tests 8-11). The reason for not adding the value-sum check is correct: `value` is source_gw while prices are live. |
| C4 model and mapping | **Resolved** | Fixed `BASELINE_MODEL = "sim-v1"`, never chosen by recency. Deadline from the bootstrap. Mapping as of `run.created_at`, with `mapping_snapshot_at` honestly stated as an upper bound (§7.1). Tests 14-16. This also covers my FAA-15 condition that an M02 run is never picked. |
| C5 null policy | **Resolved** | §7.2 has one policy. Run-level failures null all players. Per-player matching gives `aligned`/`partial`. Duplicates are unmatched with reasons. Tests 17-18. |
| C6 cache | **Resolved** | §6: a hit recomputes projection and freshness, reports `cached_at` and age, caps at 64, and expires at the deadline. Tests 19-24. |
| C7 lock and time | **Resolved, one correction** | Non-blocking lock before any cache lookup or fetch; refuses during `jobs.busy()`; 25 s budget; tests 25-29. Correction T1 below. |
| C8 personal data | **Resolved** | §4 treats the entry ID as personal data. §8 never puts `str(exc)` in responses or logs (correct: `SourceUnavailable` and `SourceFormatError` both embed the URL). `_safe_reason` redaction. Test 34 covers responses and log records. |
| C9 errors and test folder | **Resolved, one correction** | picks 404 → `no_squad_yet`; history 404 → `entry_not_found`, read from the status, never parsed from text. The test folder inside the workspace, with failure reporting, follows god's instruction. Correction T2 below. |

**T1. The worst-case time claim is wrong (§3.3 L87-89).** With `retries=1`, `FPLClient._get` makes **two** attempts,
each up to `T`, plus a wait of up to `MAX_WAIT`. One call can therefore take `2T + 2` s. With 7 s of budget left,
`T = 7` and the call can run 16 s, so the import can end at about 34 s, not "≤ 25 + 2 s". It is still bounded, not
minutes. Fix either:
- the statement (worst case about budget + 2·8 + 2 s), or
- the rule (`T = min(8, (remaining − MAX_WAIT) / 2)`).
Test 28 should cover a slow first attempt plus a retry.

**T2. `uv run` can install packages (§11 test 38).** By default `uv run` syncs the environment, which can install or
change packages. That conflicts with "no dependency is installed" and with the current incident containment. Use
`.venv/Scripts/python.exe -m pytest …`, or `uv run --frozen --no-sync pytest …`.

**Recommended:** add a warning when the selected run was created before the `source_gw` deadline. Such a run is
valid but predates last week's results and news. The r1 contract had this check; r3 dropped it silently.

Contract review complete. Next: code review at Pablo's implementation handoff, focused on tests 25-29 (lock and
time), 34 (redaction), 14-18 (projection policy) and T1/T2.
