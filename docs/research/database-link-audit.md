# Database link audit (FAA-3)

Auditor: Wegz (wegz-mvpmvdzz), 2026-10-26. Scope: Pablo's evidence-link work (FAA-4/5/6):
`src/fpl_agent/db/link_candidates.py`, `player_links.py`, `link_audit.py` (new, untracked), the
tracked diff `artifacts/db-links-20261026/final-tracked-diff.patch` (database.py, schema.py,
features.py, project.py, cli.py), and tests `tests/test_link_candidates.py`,
`tests/test_player_links.py`, `tests/test_link_audit.py`. Method: code-review-and-quality five-axis
review plus the leakage-reviewer checklist, synthetic reproductions and mutation tests on isolated
scratch copies. No implementation file and no original database was changed.

## Verdict: APPROVE after re-review (2026-10-27; see "Re-review r2" at the end). First review: REQUEST CHANGES

The core is careful and mostly well tested: the candidate rule is conservative, apply/undo is
transactional, readers never double count a match, and the reported numbers reproduce. But three
paths named in the brief do not follow links or time correctly, and each is reproduced below:
conflict exclusion ignores linked pairs, active links are never re-checked, and `valid_from`
ignores when the evidence and the link were recorded. These are required before acceptance; they
are not approved deferrals.

## What reproduced (Pablo's claims)

| Claim | Independent result |
|---|---|
| Linked minutes 70.3% to 97.4% (season 2026, dev copy) | Reproduced on scratch copies via `fpl-agent db-links`: orig 293/449 players, minute share 0.7031; linked 424/449 (131 by evidence), 0.9740. |
| Exact undo | Reproduced for data: every table in `work-undo` matches the original by row count and content hash, except `player_links` (131 rows, all rolled back) and five empty tables the `schema="init"` open created on the legacy file (`repair_log`, `source_attempts`, `fpl_deadlines`, `player_codes`, `migration_log`). Coverage after undo: 0.7031. |
| Earlier cutoffs unchanged | True but weak on this data: all 131 links have `valid_from` 2026-10-21, because the only 2026 squad snapshots are two observed pulls on 2026-10-20. Every gameweek cutoff before that sees no link. |
| Original database untouched | `data_cache/football.duckdb` sha256 `9bc925c1...5e1d` equals `orig-copy.duckdb`. |
| 839 passed, 92.80% coverage, lint clean | See Verification. |

## Findings (most severe first)

### 1. High: conflict exclusion does not follow links (validated runs keep contradicted rows)
`src/fpl_agent/db/quality.py:233-257` (`source_conflicts`) joins FPL rows to FotMob
`appearances`/`player_match_stats` on the **same** `player_id`. For an evidence-linked pair the FPL
row is under `to_player_id` and the FotMob row under `from_player_id`, so the join never meets.
`eligibility.excluded_fixtures` (`eligibility.py:131-143`) and `validated_inputs` (`:146-168`)
therefore never exclude a linked player's disagreeing match, while `player_links.resolve` credits
that row to the FPL player.
Reproduced (R1): link applied, then match 4 arrives with FPL 90 min + 1 goal vs FotMob 20 min,
0 goals. `source_conflicts(db) == []`, `excluded_fixtures(db) == (set(), set())`, and the resolved
frame contains (match 4, p). On the dev copy today the count is 0, so this is latent, not yet
harmful. Pablo's doc lists it as a remaining gap; the brief says it is not an approved deferral.
Fix: in `source_conflicts`, map FotMob ids through active `player_links` (join
`l.from_player_id = s.player_id AND l.to_player_id = f.player_id AND l.rolled_back_at IS NULL`, as
an alternative to the direct id match); add a test that a linked conflict is excluded.

### 2. High: active links are never re-checked; the documented re-check does not work
`link_candidates.py:150` drops every player in `linked_to` from `unlinked`, and `:153`/`:187`
treat a linked record as taken. Once applied, a pair is never evaluated again: later evidence that
contradicts it changes nothing, nothing reports it, and readers keep using it.
Reproduced (R2): after the link, a later match where FotMob has the record playing 90 min for the
player's side while FPL says the player sat out. Without the link the rule now rejects the pair
("contradicted by the evidence"); with it, `propose` returns the player in neither accepted nor
rejected, `coverage` still counts it, the batch stays active.
`docs/research/database-strengthening.md:185-186` says "Re-running `--propose` shows whether a
linked pair would still be accepted"; R2 shows it does not. Fix: a read-only re-validation
(evaluate active pairs with their links ignored, report any no longer accepted, with reason) wired
into `db-links` and the `quality` checks as a warning; correct the doc.

### 3. High: `valid_from` uses kickoff dates only, not when evidence or the link was recorded
`link_candidates.load` (`link_candidates.py:51-89`) filters FPL rows by `kickoff < cutoff` and snapshots by
`snapshot_at < cutoff`, and FotMob appearances not at all by time; `ingested_at` (present on
`appearances` and `fpl_points_gw`) is ignored, and `player_links.resolve` (`player_links.py:122`)
compares only `valid_from`, never `recorded_at`. So:
- a link recorded on 2026-10-26 is used by any replay or backtest cutoff after its `valid_from`,
  validated mode included (R3: cutoff = `valid_from` + 1 day, earlier than `recorded_at`, resolves
  the record to the player);
- evidence backfilled after the fact, or a later FPL correction (`fpl_points_gw` is upserted in
  place), moves `valid_from` earlier than anything the decision could have known; `history`
  re-runs the rule on today's values for past dates.
Validated evaluations elsewhere require observation by the cutoff (odds `ingested_at <= cutoff`,
observed snapshots only, `eligibility.py:53-56, 69-73`). The plan says past cutoffs must not see
links "inferred or recorded from future evidence". Fix: give `resolve` the evaluation mode; in
validated mode require `recorded_at <= cutoff` (and evidence `ingested_at < valid_from`); keep the
kickoff-based rule for exploratory runs and label it. Add a test with a link recorded after a
validated cutoff.

### 4. Medium: reconstructed squad snapshots count as team evidence
`link_candidates.py:73-76` takes every `fpl_players_gw` row with `snapshot_at < cutoff`. Archive
(vaastav) rows are kickoff-stamped reconstructions (`features.OBSERVED_SNAPSHOT`), which the
eligibility code refuses as pre-deadline evidence. Reproduced (R4): with only reconstructed
snapshots, `history` accepts the pair. Fix: use observed snapshots for team evidence (or for
`valid_from`) at least in validated mode; test it.

### 5. Medium: coverage and the reader are not season-scoped
`link_audit._links_by_target` (`link_audit.py:29-40`) ignores `player_links.season`, so a 2026
link marks the player linked in any season's report. Reproduced (R7): the player's 2025 minutes
count as linked (minute share 1.0) although the record has no 2025 FotMob row. The reader join
`features.py:206-212` is also season-less, crediting the record's rows from every season; that is
defensible (identity is time-invariant) but undocumented and untested. Fix: scope coverage to links
of that season or to records with rows in it; state and test the reader's cross-season intent.

### 6. Medium: `player_links` ids are outside the reference registry and orphan checks
`quality.REFERENCES["player"]` (`quality.py:315-320`) lacks `player_links.from_player_id` and
`to_player_id`; the registry test (`tests/test_quality.py:316-320`) only scans columns literally
named `player_id`, so it cannot catch this (R6). A future merge or delete of a player would leave
links pointing at a missing or wrong id, unreported. Fix: add both columns to the registry and
widen the test's id-column set.

### 7. Medium: test gaps shown by surviving mutants
18 mutants on an isolated copy, focused suite (25 tests) per mutant, file restored after each
(verified identical to the repo). 11 killed, 7 survived:
- M5 `history`: never resetting `since[pair]` (so `valid_from` would be the first acceptance, not
  the start of the final uninterrupted run): **survives**. This is the temporal guarantee itself.
  Need a test where a pair is accepted, rejected, then accepted again.
- M8 the "record played while FPL says the player sat out" check (`agg["absent"] == 0`):
  survives. Need a test.
- M2 the player-side agreement threshold (`hits >= AGREEMENT * n_p`): survives. Need a test where
  the record covers only part of the player's appearances.
- M15 `coverage(at=...)` ignoring `valid_from`: survives. Need a test.
- M3 (FPL `kickoff < cutoff` in `load`), M13 (post-insert duplicate check) and M14 (held-link
  conflict in `apply`) survive as equivalent: `propose` re-filters by kickoff, and proposals never
  contain linked ids. FYI: M13/M14 are unreachable defensive code; test them by inserting a
  conflicting link directly, or drop them.
Killed: M1 MIN_SHARED, M4 snapshot cutoff, M6 rivals, M7 reverse uniqueness, M9 conflicting
identity, M10 `valid_from` gate, M11 no-double-count, M12 rollback filter, M16 projection uses
`resolve`, M17 undo, M18 stat contradiction.

### 8. Low: redo in the same second overwrites the undo record
Batch id is `links-<digest>-<second>` (`player_links.py:71-72`) and rows are upserted on
`link_id` (`:83`). Apply, undo, apply again within one second (same pairs and cutoff) gives the same
ids: the rolled-back rows are replaced, and the history shows one active batch with no trace of the
undo (R5). Fix: add a random or sequence suffix to the batch id, or insert rather than upsert.

### FYI
- Precision is not established. Names of all 131 accepted pairs were compared: 129 share a name token (consistent with, not proof of, one person). The two that do not
  (Beto / Norberto Bercique Gomes Betuncal, 2 matches; Costinha / João Pedro Loureiro da Costa,
  6 of 6 matches) look plausible but are not independently confirmed. 18 links rest on the minimum
  2 shared matches.
- Translation path: `fit-translations` (`cli.py:452`) reads the unresolved frame. FotMob records
  already span competitions, so mover detection is unaffected. This is correct; no change needed.
- Double counting: `resolve` drops a linked row where the player has their own row for that match
  (mutant M11 killed). No linked player holds a direct FotMob id on the dev copy.
- `--apply` opens with `schema="init"`, so applying to a legacy file also creates the missing empty
  tables; undo does not remove them. Harmless, but "exact undo" means data and reader views, not
  the file.
- Unrecorded deferral in Pablo's doc: five name-linked players whose FPL and FotMob teams differ
  predate this work; out of scope here.

## Verification (commands run, results)
All runs were in temporary copies of the repo and databases. Durable evidence (logs, harness, reproductions, README with rerun steps) is in `artifacts/db-links-audit-20261026/`.
- Full suite: `uv run pytest -q --cov --cov-report=term-missing --cov-fail-under=85 -p no:cacheprovider --basetemp=<tmp>` → (`full-suite.log`) 793 passed, 13 failed, 8 skipped; coverage 92.80% (gate 85% met). All 13 failures and the 30 tests not collected (814 vs 844) came from my copy lacking `.github/` and `scripts/`. With both copied in, `pytest tests/test_workflows.py tests/test_source_health.py` → 60 passed, 1 skipped (`workflows-rerun.log`), and collection is 844/857 (13 deselected), the same as the repo. No failure is attributable to the change. This agrees with Pablo's log (839 passed, 5 skipped, 92.80%).
- Lint (clean copy, src verified identical to repo): `uv run ruff check .` → All checks passed; `uv run ruff format --check .` → 138 files already formatted.
- Focused: `pytest tests/test_link_candidates.py tests/test_player_links.py tests/test_link_audit.py` → 25 passed. (Without `--basetemp`, one test errors: the default pytest temp folder is access-denied on this machine. Environment, not code.)
- Reproductions: `repro/test_repro.py` (R1-R7, each asserts the current defective behaviour) → 7 passed. File: `artifacts/db-links-audit-20261026/test_repro.py`; after a fix, the matching R-test should fail.
- Mutations: `artifacts/db-links-audit-20261026/mutate.py` → `mutations.log` (11 killed, 7 survived).
- Coverage numbers: `fpl-agent db-links --db <copy> --season 2026` on copies of `orig-copy`, `work`, `work-undo`.

## Required before acceptance
Findings 1-7 (fixes plus tests) and correcting `database-strengthening.md:182-186`. Finding 8 is
recommended. Re-review after the fixes.

## Re-review r2 (2026-10-27): APPROVE

Reviewed the current code against my audit snapshot: `link_candidates.py`, `player_links.py`,
`link_audit.py`, `quality.py`, `features.py`, `project.py` and the new tests; Pablo's evidence in
`artifacts/db-links-20261026/r2/`. Independent checks were run on fresh scratch copies of the repo
and of the r2 databases. Evidence: `artifacts/db-links-audit-20261026/r2/`. No implementation
file or original database was changed: `data_cache/football.duckdb` sha256 is still `9bc925c1...5e1d`.

| # | Finding | Fix (file:line, current) | Verified by | Verdict |
|---|---|---|---|---|
| 1 | Conflicts ignore links | `quality.py:250-289`: `source_conflicts` also compares FPL rows with the FotMob rows of an active linked record (`linked_record`) | R1 now fails; `test_a_conflict_on_a_linked_record_excludes_the_match` (match 4 excluded); mutant W8 killed. r2 copy: 3 conflicts, 0 via links, same as the original | Fixed |
| 2 | Stale active links | `link_candidates.py:248-276` `revalidate` re-runs the rule with all links set aside. Its results are used by `player_links.unsupported` (`:112-123`), projection (`project.py:272-276`), coverage (`link_audit.py:61-64`) and a `player_link_unsupported` quality warning (`quality.py:221-230`) | `test_later_contradicting_evidence_suspends_the_link_from_then_on`; my UTC tests below; mutants W2, W3, W4, W5, W12 killed. r2 copy: `revalidate` = [] | Fixed |
| 3 | Recorded/ingested time | `resolve` requires `recorded_at <= decided` and fails closed when it is missing (`player_links.py:126-141`); `load`/`propose` require `ingested_at < cutoff` for FPL and FotMob rows and drop untimed rows (`link_candidates.py:54-119`); `history` also steps on ingestion dates (`:224-229`) | R3 now fails; `test_a_past_decision_never_uses_a_link_written_after_it`, `test_evidence_counts_only_once_stored_and_missing_times_never_count`, `test_valid_from_waits_for_late_stored_fotmob_rows`; mutants W1, W7, M15 killed | Fixed |
| 4 | Reconstructed snapshots | `link_candidates.py:92`: team evidence comes from observed snapshots only (`OBSERVED_SNAPSHOT`) | `test_reconstructed_snapshots_are_not_team_evidence`; mutant W6 killed. (R4 now fails at its setup step, because the test helper writes observed snapshots; the targeted behaviour is covered by the test and W6.) | Fixed |
| 5 | Season scope | `link_audit.py:32-46`: links are filtered by `season`; the reader stays cross-season by design, documented at `player_links.py:132-134` and tested | R7 now fails; `test_coverage_is_per_season_and_the_reader_follows_identity_across_seasons`; mutant W9 killed | Fixed |
| 6 | Registry and orphans | `quality.py` REFERENCES and orphan checks cover `player_links.from/to_player_id` (`:86-91`); the registry test matches any `*_player_id` column (`test_quality.py:310-321`) | R6 now fails; orphan assertion in the season test; mutant W10 killed | Fixed |
| 7 | Test gaps | New tests for the history reset, absent check, player-side threshold and `coverage(at=)` | Earlier survivors M2, M5, M8, M15 are now all killed | Fixed |
| 8 | Undo journal | `player_links.py:60-80`: unique batch suffix, plain INSERT (never overwrite) | R5 now fails; `test_apply_undo_apply_in_one_second_keeps_every_batch`; W11 killed. On an r2 copy: undo → reapply (new batch, 131) → undo leaves both batches on record, each rolled back | Fixed |

**The R2 reproduction mismatch.** My original R2 still passes, but because of the test, not the code.
Its contradiction rows were stamped with DuckDB `now()`, which writes local wall time (+04:00 on
this machine). `revalidate` compares against naive UTC, so those rows are four hours "in the
future" and correctly not yet stored. Real ingestion stamps `ingested_at` with
`database.now()` (naive UTC; `ingest_fotmob.py:131`, `ingest_fpl.py:216`, `ingest_vaastav.py:80`,
`ingest_understat.py:23,55`), and no product code writes `ingested_at` from SQL `now()`. Both
directions, checked in `r2/test_rereview.py` (4 passed):
- a UTC-stamped contradiction withdraws the link now: `revalidate`, coverage and the projection
  reader all drop it;
- the same rows stamped at local time (UTC+4h) are ignored until that moment, then withdraw the
  link;
- an untimed contradiction still withdraws it (fail-safe for withdrawals);
- a decision before the contradiction was stored keeps the link. My test of this is vacuous
  whenever the link was recorded after the cutoff; Pablo's
  `test_later_contradicting_evidence_suspends_the_link_from_then_on` asserts it with explicit times.

**Tests run (scratch copy, `--basetemp` outside OneDrive):**
- Focused: link, quality and persistence test files → 135 passed, 4 skipped.
- `tests/test_persistence.py` alone → 32 passed, 4 skipped.
- Lint: `ruff check` and `ruff format --check` on src/tests → clean.
- Full suite: not rerun, per the team agreement. Pablo's r2 log shows 851 passed and 1 failed, with
  coverage 92.84%. That one failure, `test_reordered_columns_load_by_name`, is environmental: a
  `PermissionError` on `Path.replace` inside an OneDrive-synced `--basetemp`
  (`artifacts/.../r2/pt-full2`). It passes in a local temp folder, as do Pablo's
  `persistence-rerun.log` and my run above. It is not a product failure.
- Mutations (`r2/mutate2.py`, `r2/mutations-r2.log`): 16 of 16 killed, and the files were verified
  identical to the repo afterwards. Note: W8 is killed by a SQL parameter error (53 failures), not
  by a clean assertion; Pablo's N10 kills the same mutant cleanly.

**Behaviour to know (FYI, not blocking):**
- Requiring `recorded_at <= decision` means every replay or backtest before 2026-10-26 uses no
  evidence link. Pablo's model-effect check shows 0 changed projections at past cutoffs and 473 of
  668 at GW9 now, with undo identical to the original. Historical evaluations gain nothing from
  these links, by design.
- Conflict exclusion follows active links whenever they were written. So applying a link can
  remove a match from a past validated backtest, which shifts results that were reported earlier.
  This removes data and never leaks, but results should cite the link batch state.
- `unsupported` revalidates only links of the decision's season. A link from another season,
  credited across seasons, is not re-checked against this season's evidence. Consider adding that
  when multi-season links exist (there are none today).
- `revalidate` re-runs the full rule for each projection once a usable link exists. Fine at the
  current size; watch backtest runtime.
- Test fixtures still stamp rows with DuckDB `now()` (local time). Tests near a time boundary
  should use explicit stamps, as Pablo's key tests do.
- Precision is unchanged and not established. 129 of 131 pairs share a name token; Beto and
  Costinha remain unconfirmed identities.

**Verdict:** every required finding (1-7) and the recommended finding (8) is fixed and verified
independently. No Required or Critical findings remain. FAA-3 can be accepted by god.
