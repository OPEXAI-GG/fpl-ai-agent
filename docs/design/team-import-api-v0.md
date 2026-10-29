# Team import API v0 — contract (local, read-only)

Status: **contract only, no code yet** (FAA-25, Pablo). Revision 3, 2026-10-29: resolves the
open items of Wegz's Re-review r2 (`artifacts/team-import-contract-review.md`); disposition in
§12. Frozen after handoff until Wegz's re-review r3. Implementation, code review (Wegz) and
acceptance (god) follow separately.

Rule references in this document describe **what the repository code does**
(`rules/engine.py`, `rules/*.yaml`). They are not verification of current FPL rules, terms of
use or the legality of the season's settings; that needs a separate check.

## 1. Purpose and limits

Given an FPL entry (team) ID, return that team's squad state as FPL's public endpoints describe
it, plus the existing stored baseline projections for those players **only when they provably
belong to the same season, gameweek and model run**.

Not in scope, and not claimed by this endpoint:

- Hosted or multi-user use. The route runs inside the existing local dashboard
  (`127.0.0.1`, one user, per-start token). It is **not evidence of hosted multi-user
  isolation**, account separation, rate limiting or abuse protection on the public internet.
- Accounts, entitlements, payments, FPL sign-in, the post-transfer "current" squad, or any
  write to FPL.
- Running a model. The endpoint reads projections already in the database; it never creates
  them.
- Forecast quality. While TASK.md M00 records 0 eligible gameweeks for validation, every
  projection is labelled `exploratory`.

## 2. What exists today (verified by reading the code)

| Piece | Where | Relevant behaviour |
| --- | --- | --- |
| Squad parsing | `data/entry.py` `parse_squad()` | Builds `SquadState` from public picks, transfers, history and bootstrap. Purchase price = latest transfer-in cost outside Free Hit weeks, else `now_cost - cost_change_start`. Raises `KeyError` if a pick is missing from bootstrap. Does not set `active_chip`. |
| Source gameweek | `agent.py` ~229–237 | Picks for `next_gw - 1`; one week earlier if that week was a Free Hit and `next_gw - 1 > 1`; no picks before GW2. Not reusable yet. |
| FPL fetches | `data/fpl_client.py` | Default 30 s timeout, 4 retries, waits up to `MAX_WAIT = 30` s. Any HTTP status < 429 raises `SourceUnavailable("GET <url>: HTTP <code>")`, the URL **containing the entry ID**; the status is on `exc.__cause__.response`. |
| Shape checks | `contracts.py` | No checks for entry history, picks or transfers yet. |
| Projections | `db/schema.py` `projections(run_id, model, created_at, season, gw, player_id, xpts, detail)`; `model/project.py` `MODEL = "sim-v1"` | Several models' runs can coexist for one gameweek. |
| As-of mapping | `db/database.py` `fpl_players_as_of(season, gw, as_of)` | Newest `fpl_players_gw` row per `fpl_id` with `snapshot_at <= as_of`. |
| Dashboard read model | `dashboard/service.py` | Read-only DuckDB; promises no network; `STALE_SNAPSHOT_HOURS = 6`; `_safe_reason` redacts secrets only. |
| Server | `dashboard/server.py` | `ThreadingMixIn`: requests run concurrently. POST needs same Origin + `X-Dashboard-Token`; 4 KB body cap; `jobs.busy()` says a local refresh/plan is writing. |

Design consequences:

1. FPL fetching lives in a new module `src/fpl_agent/dashboard/team_import.py`, so `service.py`
   keeps its no-network promise. The FPL client is injected so tests never use the network.
2. FPL element IDs are not projection keys; mapping is per §7.
3. **Ownership (god, 2026-10-28):** Pablo owns the entry history / picks / transfers checks in
   `contracts.py`, the `entry.py` source-gameweek helper, `team_import.py`, the route and the
   route-specific tests. Nabil keeps all other data and model ownership.
4. Season is not in FPL's bootstrap; it is the configured season with consistency checks (§5.1,
   accepted by Wegz r2 as C1).
5. `fpl_client.py` and `agent.py` are **not** changed in v0. Short timeouts are achieved by
   injecting a configured client (§3.3); `agent.py` adoption of the shared helper is deferred.

## 3. Request, concurrency and time limits

### 3.1 Request

`POST /api/team-import` — POST because each call makes outbound requests to FPL; the existing
Origin + token guard stops another site from triggering them.

```json
{ "entry_id": 1234567 }
```

- `entry_id`: JSON integer, `1 <= entry_id <= 99_999_999`. Strings, floats, booleans,
  negatives, extra keys → `400 invalid_request`, before any lock or FPL call.
- No other inputs. Season is configured; gameweek and deadline come from FPL's bootstrap.

### 3.2 One import at a time (atomic)

- A module-level `threading.Lock` is taken with `acquire(blocking=False)` **before** any cache
  lookup or FPL call. Not acquired → `409 busy`. Released in `finally` on every path.
- Imports are also refused with `409 busy` (`reason: "local job running"`) while
  `jobs.busy()` is true, because a refresh or plan may be rewriting the tables the import reads.
- No check-then-set flags.

### 3.3 Time limits

- The import builds its own `FPLClient(client=httpx.Client(timeout=T), retries=1)` from a
  small subclass in `team_import.py` that sets `MAX_WAIT = 2` (class attribute; no change to
  `fpl_client.py`).
- Whole-request budget **25 s**, measured with an injectable monotonic clock from lock
  acquisition. Before each of the (at most four) FPL calls, the per-call timeout `T` is set to
  `min(8 s, remaining budget)`; if remaining budget is under 1 s, the import stops with
  `503 upstream_unavailable` (`reason: "time budget exceeded"`). Worst-case wall time is
  therefore bounded by the budget plus one in-flight call's final wait (≤ 25 + 2 s), not
  minutes.
- Fetch order: bootstrap → history → transfers → picks.

## 4. Response (200)

```json
{
  "api_version": "team-import/v0",
  "identity": {
    "entry_id": 1234567,
    "season": 2026,
    "next_gw": 10,
    "next_deadline": "2026-10-31T11:00:00Z",
    "source_gw": 9,
    "source_gw_reason": "last deadline | free hit in GW9: reverted to GW8 | preseason",
    "fetched_at": "2026-10-28T20:00:00Z",
    "cached": false, "cached_at": null, "cache_age_seconds": null
  },
  "state": {
    "bank": 5, "value": 1003, "active_chip": null,
    "free_transfers": 2, "already_transferred": 0,
    "chips_available": {"bboost": [10, 19]},
    "chips_used": [["wildcard", 4]],
    "basis": {
      "bank": "source_gw", "value": "source_gw", "active_chip": "source_gw",
      "free_transfers": "history up to next_gw, before already_transferred moves",
      "chips": "history up to next_gw - 1",
      "already_transferred": "live transfers list",
      "now_cost": "live bootstrap at fetched_at",
      "selling_price": "estimate: live now_cost + reconstructed purchase price"
    },
    "selling_price_basis": "estimated from public data",
    "rules_file": "2026_27.yaml", "rules_exact": true
  },
  "squad": [
    {
      "fpl_id": 351, "player_id": 4021, "name": "…", "position": "MID", "team_id": 12,
      "pick_position": 7, "is_captain": false, "multiplier": 1,
      "now_cost": 81, "purchase_price": 78,
      "purchase_price_source": "transfer | opening price",
      "selling_price": 79, "status": "a", "chance_next": 100,
      "projection": {"xp": 4.6, "q10": 0.9, "q90": 10.2}
    }
  ],
  "projection": {
    "status": "aligned | partial | misaligned | missing | stale",
    "label": "exploratory",
    "model": "sim-v1", "run_id": "…", "season": 2026, "gw": 10, "created_at": "…",
    "mapping_snapshot_at": "…",
    "matched_players": 15, "unmatched": [],
    "reason": ""
  },
  "freshness": {
    "db_snapshot_at": "…", "db_snapshot_age_hours": 3.2, "db_snapshot_stale": false,
    "stale_after_hours": 6
  },
  "warnings": []
}
```

Money is in FPL tenths (81 = £8.1m). `name`, `status`, `chance_next` come from the live
bootstrap.

**Personal data.** An FPL entry ID publicly resolves to a manager's name and history, so
`entry_id` is personal data. v0 **minimises** it: no manager name, email or league data is
returned; the ID appears only in the response to the caller; it is kept only in the in-memory
cache (§6) and never written to disk, the database, logs or error text (§9). Hosted use needs a
privacy review.

## 5. Squad identity and field timelines

### 5.1 Identity rules

The response is valid only if all of these hold; otherwise it is an error (§9), never a
partial guess:

1. `next_gw` and `next_deadline` come from the bootstrap fetched in this request (`is_next`
   event, its `deadline_time`). `season` is the configured season; the import refuses with
   `409 season_mismatch` unless the newest season in `fpl_players_gw` equals it and the
   bootstrap's first event deadline falls in July–December of that year. These are consistency
   checks, not proof of the official season.
2. `source_gw` is chosen by a pure helper in `entry.py` implementing the agent's rule exactly:
   - `next_gw == 1` → no source gameweek → `409 no_squad_yet`;
   - otherwise `source_gw = next_gw - 1`;
   - if history shows a Free Hit at `next_gw - 1` **and** `next_gw - 1 > 1`, then
     `source_gw = next_gw - 2` (squad, bank and value revert to the pre-Free-Hit week);
   - a Wildcard, Bench Boost or Triple Captain at `next_gw - 1` does not revert.
3. Picks, history and transfers are all for the requested `entry_id`. Picks must contain
   exactly 15 picks with unique element IDs, 2 GKP / 5 DEF / 5 MID / 3 FWD (positions from the
   same bootstrap).
4. Every pick's element ID exists in the same bootstrap. A missing one is
   `502 upstream_contract`, not an uncaught `KeyError`.

### 5.2 Field timelines

| Field | Timeline | Source |
| --- | --- | --- |
| squad membership, `pick_position`, `is_captain`, `multiplier` | `source_gw` (last week's captaincy, not next week's) | picks of `source_gw` |
| `bank`, `value` | `source_gw`; after a Free Hit, the reverted week `next_gw - 2` | `picks.entry_history` |
| `active_chip` | chip played **in `source_gw`** (from the picks payload). A chip pending for `next_gw` is not public and is never reported | picks of `source_gw` |
| `free_transfers` | start of `next_gw`, **before** any `already_transferred` moves | `free_transfers_at(history, next_gw)` |
| `chips_used`, `chips_available` | history events `<= next_gw - 1` | history |
| `already_transferred` | transfers with `event >= next_gw` (counted, not applied) | live transfers list |
| `now_cost`, `name`, `status`, `chance_next` | now | live bootstrap at `fetched_at` |
| `purchase_price`, `selling_price` | reconstructed (§5.3) | transfers + live bootstrap |

`already_transferred > 0` adds warning `transfers_pending`: the squad, bank and free transfers
shown are **before** those moves (the post-transfer squad needs sign-in).

### 5.3 Prices

- Computed by the existing `parse_squad` / `selling_price` with `Rules.nearest(season)`. No new
  pricing logic. `rules_file` and `rules_exact` are reported; `rules_exact: false` adds a
  warning.
- **Selling prices are estimates, not FPL's figures.** FPL shows a team's real selling prices
  only after sign-in, which is out of scope.
  - `purchase_price_source: "transfer"`: cost of the latest public transfer-in of that player
    outside a Free Hit week.
  - `purchase_price_source: "opening price"`: `now_cost - cost_change_start`. This is correct
    only for a player held since the entry's first gameweek **and** an entry that started in
    GW1.
  - **Late joiner:** if the entry's first event in history `current` is after GW1, warning
    `late_joiner_price_estimate` names the affected players (those with `opening price`); their
    purchase and selling prices may be wrong because the squad was bought at that week's
    prices, not opening prices.
  - Selling price = repository rule in `rules/engine.py`: keep half of a rise, rounded down; a
    fall is taken in full.

## 6. Cache

- In memory only, cleared on shutdown; never written to disk.
- Key `(season, entry_id, next_gw, next_deadline)`. At most **64** entries; inserting a 65th
  evicts the oldest by `fetched_at`.
- What is cached: only the FPL-derived parts (identity, state, squad without projections).
- Expiry: the earlier of 10 minutes after `fetched_at` and `next_deadline` (injectable clock).
  An expired entry is deleted and never served.
- A miss fetches a bootstrap; if its `season`, `next_gw` or `next_deadline` differ from any
  cached entry's, **all** entries are dropped before answering.
- A hit still takes the lock and checks `jobs.busy()`, makes no FPL call, and **recomputes**
  `projection` and `freshness` from the database on every request. It returns `cached: true`,
  `cached_at` and `cache_age_seconds`.
- Limit: prices in a cached answer can be up to 10 minutes old; `fetched_at` shows this.
- Errors are never cached.

## 7. Projection selection and attachment

### 7.1 Run selection (run-level)

1. Model: `BASELINE_MODEL = "sim-v1"` (the value of `model/project.py` `MODEL`), a constant in
   `team_import.py`. Changing it to a candidate (e.g. M02) requires god's acceptance; it is never
   chosen by recency.
2. Run: the newest `run_id` in `projections` with `model = BASELINE_MODEL`, `season`,
   `gw = next_gw`, and `created_at < next_deadline` (from the bootstrap, not an approximate
   deadline). Only that run's rows are used; nothing is mixed across runs, models or seasons.
3. Mapping: `fpl_id → player_id` from the rows the run could have seen — the same query as
   `fpl_players_as_of(season, next_gw, run.created_at)`, run read-only on the dashboard's
   connection. `mapping_snapshot_at` = the newest `snapshot_at` in that result.
4. Limit stated in `reason` when it applies: the database does not record which snapshot a run
   used, so `mapping_snapshot_at` is the newest snapshot available at run time, an upper bound,
   not proof of the run's input.

### 7.2 One policy for null projections

Run-level outcomes null **every** player's `projection`:

| Status | When |
| --- | --- |
| `missing` | No baseline run for `(season, next_gw)` before the deadline |
| `misaligned` | Season/gameweek mismatch, run at or after the deadline, empty as-of mapping, or 0 of 15 picks matched |
| `stale` | The run's mapping snapshot is older than `STALE_SNAPSHOT_HOURS` (6 h) at `run.created_at` |

If the run-level checks pass, matching is per player:

- A pick is **matched** when its `fpl_id` maps to exactly one `player_id` in the as-of mapping,
  no other pick maps to the same `player_id`, and that `player_id` has a row in the run.
- Otherwise it is **unmatched** with a reason: `no_mapping`, `duplicate_mapping` (two or more
  `player_id`s, or a shared `player_id`; never "first row wins") or `no_projection_row`.
- 15 matched → `aligned`. 1–14 matched → `partial`: matched players keep projections,
  unmatched ones are `null`, and `unmatched` lists `{fpl_id, reason}`.

`freshness.db_snapshot_*` reports the database's newest snapshot against now; it is
informational and does not change the status. `label` is always `exploratory` in v0; it changes
only when Nabil's validation (TASK.md M01–M03) reports eligible evaluation and god accepts it.

## 8. Logging and redaction

- `team_import.py` never puts `str(exc)` from FPL calls into responses or logs, because
  `SourceUnavailable` messages contain the URL with the entry ID.
- Error responses use fixed messages per error code. Local logs record only the error code,
  exception type, which call failed (`bootstrap|history|transfers|picks`) and HTTP status.
- The 500 path logs a fixed message plus exception type; no traceback text containing URLs is
  written. `service.py` `_safe_reason` additionally replaces any `entry/<digits>/` path with
  `entry/[redacted]/` as defence in depth.

## 9. Error cases

All errors: `{"error": "<code>", "message": "<fixed text>"}`; no stack traces, no upstream
bodies, no entry ID.

| HTTP | `error` | When |
| --- | --- | --- |
| 400 | `invalid_request` | Bad JSON, body over 4 KB, bad or missing `entry_id`, extra keys |
| 403 / 421 | `forbidden` / existing | Existing Origin / token / Host guard fails |
| 404 | `entry_not_found` | The **history** endpoint returns 404 |
| 409 | `busy` | Lock not acquired, or `jobs.busy()` |
| 409 | `no_squad_yet` | `next_gw == 1`, or the **picks** endpoint returns 404 for `source_gw` (entry joined after it), or picks empty |
| 409 | `season_mismatch` | Configured season disagrees with DB or bootstrap (§5.1) |
| 409 | `season_rollover` | Bootstrap has no `is_next` event (season over or not started) |
| 502 | `upstream_contract` | FPL payload fails `contracts.py` checks, pick missing from bootstrap, squad not 15 valid picks, other 4xx |
| 503 | `upstream_unavailable` | Network error, timeout, 429/5xx after one retry, non-JSON, or time budget exceeded |
| 500 | `internal` | Anything else |

404 detection reads the HTTP status from `exc.__cause__.response.status_code` of
`SourceUnavailable`; the message text is never parsed.

## 10. Dependencies and owners

| Need | Owner | Status |
| --- | --- | --- |
| `contracts.py` checks for entry history, picks, transfers | Pablo (god decision 2026-10-28, this slice only) | Assigned; not started |
| `entry.py` source-gameweek helper, `team_import.py`, route, route tests | Pablo | Not started |
| `_safe_reason` entry-path redaction in `service.py` | Pablo (owns `service.py`) | Not started |
| Baseline model name and projection semantics | Nabil | Exists (`sim-v1`); confirm before code |
| How the result is shown in the page | Mona | Not started; API usable without UI |
| Review | Wegz. `safety-rails-reviewer` not needed (no `execution/`, `agent.py`, config changes) | Contract r3 pending |

## 11. Test acceptance

All tests offline: synthetic or recorded FPL payloads through an injected fake client or
`respx`; a temporary DuckDB built by existing test helpers; injectable clocks. No network, no
real `FPL_ENTRY_ID`. New file `tests/test_team_import.py` plus the existing dashboard tests.

**Identity and timelines**
1. Happy path: 15 picks, all matched, aligned baseline run → 200, `status: aligned`,
   `label: exploratory`, `basis` present for every state field.
2. Free Hit at `next_gw - 1` (> 1) → `source_gw = next_gw - 2`; `bank` and `value` equal the
   reverted week's `entry_history`, not the Free Hit week's.
3. Free Hit recorded at GW1 with `next_gw = 2` → no revert, `source_gw = 1`.
4. Wildcard (and Bench Boost) at `next_gw - 1` → no revert.
5. `active_chip` equals the chip in the `source_gw` picks payload; a `next_gw` chip is never
   reported.
6. `already_transferred > 0` → `transfers_pending` warning; squad, bank and free transfers are
   unchanged by those transfers.
7. `next_gw == 1` → `409 no_squad_yet`; picks 404 for `source_gw` → `409 no_squad_yet`;
   history 404 → `404 entry_not_found`.

**Prices** (expected values hand-computed in the test, not by calling `selling_price`)
8. Rise of 3 → keeps 1; rise of 1 → keeps 0; fall of 2 → full fall.
9. Player sold and bought back → purchase price is the latest buy.
10. Transfer made in a Free Hit week is ignored for purchase price.
11. Late joiner (first history event GW5) → `late_joiner_price_estimate` naming the
    `opening price` players; GW1 starter → no such warning.
12. Rules fallback → `rules_exact: false` and a warning.

**Season and projections**
13. DB newest season ≠ configured season → `409 season_mismatch`; first deadline outside
    July–December → `409 season_mismatch`; no `is_next` → `409 season_rollover`.
14. Baseline and candidate model runs for the same gameweek, candidate newer → baseline used,
    `model: "sim-v1"` returned.
15. Two baseline runs → only the newest pre-deadline run used; a run at/after the deadline is
    ignored; only a post-deadline run → `missing`.
16. Mapping as of the run: a player whose `fpl_id → player_id` row appears only after
    `run.created_at` is `no_mapping`.
17. Partial: 13 matched, one `no_mapping`, one `duplicate_mapping` → `partial`, 13 projections
    kept, 2 `null`, `unmatched` lists both with reasons.
18. All-null: wrong gameweek only → `misaligned`; no run → `missing`; mapping snapshot > 6 h
    before the run → `stale`; 0 matched → `misaligned`. Every `projection` is `null` in each.

**Cache**
19. Hit within 10 minutes → no FPL call, `cached: true`, `cached_at` and age set.
20. Hit recomputes: database changed between calls (new baseline run, older snapshot) →
    `projection` and `freshness` reflect the new state.
21. Clock past `next_deadline` but within 10 minutes → miss, fresh fetch.
22. New bootstrap with different `next_gw` or season → all entries dropped.
23. 65 distinct entries → oldest evicted; two entries never share an answer.
24. An error response is not cached (next call fetches).

**Concurrency and time**
25. Two concurrent imports with a fake client that blocks on an event → exactly one `409 busy`;
    after release a third succeeds (lock released).
26. Lock released after an exception mid-import (fake client raises) → next import proceeds.
27. `jobs.busy()` true → `409 busy`, zero FPL calls.
28. Fake clock: each call consumes 9 s → import stops before the fourth call with
    `503 upstream_unavailable`; per-call timeout set to `min(8, remaining)`.
29. Injected client has `retries == 1` and `MAX_WAIT == 2`.

**Errors, input and redaction**
30. Pick missing from bootstrap; 14 picks; duplicate pick; wrong position counts; malformed
    history/picks/transfers → `502 upstream_contract`.
31. Timeout / 5xx / non-JSON → `503 upstream_unavailable`.
32. Input validation: `"123"`, `0`, `-1`, `1.5`, `true`, `100000000`, extra key, oversize body
    → 400; zero FPL calls; lock not taken.
33. Guards: missing token, wrong Origin, foreign Host → 403/421; zero FPL calls.
34. Redaction: for every error path in 30–31 plus a 500, neither the response body nor any
    captured log record contains the entry ID digits or `entry/<id>`; `_safe_reason` replaces
    `entry/1234567/` with `entry/[redacted]/`.
35. No data from the local user's decisions, plans or settings appears in any response.
36. `service.py` read model still makes no network call (existing tests stay green).

**How tests are run**
37. `ruff check src tests` clean.
38. Focused run: `uv run pytest tests/test_team_import.py tests/test_dashboard.py
    tests/test_dashboard_controls.py --basetemp=artifacts/team-import-tests/<UTC timestamp>`,
    a unique directory inside the workspace, created fresh per run. No folder outside the
    workspace is required. If OneDrive blocks it, the exact command and error are reported to
    god with a proposed alternative for authorisation; no workaround is applied unasked. No
    dependency is installed and no package root is changed.

## 12. Disposition of review findings (Re-review r2)

| Item | r3 disposition | Where |
| --- | --- | --- |
| C1 season | Accepted in r2; unchanged | §5.1 |
| C2 timelines / Free Hit | **Fixed**: per-field timeline table and `state.basis`; captaincy is last week's; `active_chip` = `source_gw` chip; bank/free transfers before pending moves; Free Hit bank revert; GW1 Free Hit and Wildcard cases | §5.1–5.2, tests 2–6 |
| C3 late joiner | **Fixed**: `late_joiner_price_estimate` warning; label `opening price`; hand-computed price tests. Value-sum consistency check not added (r2 did not require it; `value` is `source_gw` while prices are live, so the sum would not match by design) | §5.3, tests 8–11 |
| C4 model and mapping | **Fixed**: `BASELINE_MODEL = "sim-v1"`, `model` returned; deadline from bootstrap; as-of-run mapping with `mapping_snapshot_at` and stated limit | §7.1, tests 14–16 |
| C5 null policy | **Fixed**: run-level failures null all; per-player matching gives `aligned`/`partial`; duplicates unmatched | §7.2, tests 17–18 |
| C6 cache | **Fixed**: hit recomputes projection and freshness, `cached_at`/age, cap 64, oldest evicted | §6, tests 19–24 |
| C7 lock and time | **Fixed**: non-blocking `threading.Lock` released in `finally`; refuse while `jobs.busy()`; injected client timeout ≤ 8 s, 1 retry, `MAX_WAIT = 2`; 25 s budget → 503 | §3.2–3.3, tests 25–29 |
| C8 personal data | **Fixed**: entry ID treated as personal data; wording changed to "minimises"; no `str(exc)` in responses/logs; `_safe_reason` redaction; test | §4, §8, test 34 |
| C9 errors / test dir | **Fixed**: picks 404 → `no_squad_yet`, history 404 → `entry_not_found`; tests renumbered 1–38; unique workspace-contained `--basetemp` per god's instruction (not outside OneDrive), with failure reporting | §9, tests 7, 38 |

## 13. Known limitations

- Squad is the last-deadline squad, not the live post-transfer squad.
- Selling prices are estimates from public data and the repository's rule, not FPL's figures.
- Season and rules references are checked only for internal consistency, not against current
  official FPL rules.
- `mapping_snapshot_at` is an upper bound on a run's input, because runs do not record their
  input snapshot.
- Projections are exploratory until model validation exists; FAA-7 production data is
  unverified and the local DB is a development copy.
- Local single-user route only: one import at a time, no per-user isolation, no abuse
  protection. A hosted version needs a separate contract and privacy review.
