# FAA-15 / M03: independent review of the M02 candidate (FPL xG/xA fallback)

Reviewer: Wegz (wegz-mvsc3fox), 2026-10-29. Author of the switch and tests: Nabil. Author of the inherited fallback logic:
zaza-mvpmyn3b (Codex session, 2026-10-27). Neither was written by me.

## Reviewed content (SHA-256)

| Item | SHA-256 |
|---|---|
| `src/fpl_agent/model/players.py` (current) | `9b8a2a1534869f772f8e27e5127f7faf94f6b0f40a82130a24ebb68add4b45bb` |
| `m02/players.inherited.py` (inherited, pre-switch) | `b7f477ee00f4ce7711cd1d6a965ab04a8d3d8bc10f579f640d3c478d724b73c8` |
| `src/fpl_agent/model/features.py` | `22c5abf5864e9369cbbdecda2d1b4773233bb7340a4789cfa87e69ebff20f14d` |
| `tests/test_players_fpl_fallback.py` | `d427fc0208363c1dda9bb62d98aac31506d4fd565baa0e81b643c3be4c68c983` |
| `tests/test_players.py` | `a86f3764814aa9b7436780cc8b892ff7a4160a2c54b48189b171b527d96daba2` |
| `m02/m02_compare.py` | `ce4833af9b6bc55d513deb5daa1fea5e3632911d0f5241a2860c579ec6f6a70c` |
| `m02/leakage_check.py` / `leakage-check.json` | `166db30f…fdbf5` / `7d8ea25c…ef5` |
| `m02/m02-handoff.md` / `m02-comparison.json` | `dcee4c17…821f` / `905a6883…0f06` |
| `m02/players-vs-HEAD.diff` / `players-nabil-vs-inherited.diff` | `c7162e53…a460` / `f5e59702…25c8b` |

The source fingerprint for the whole tree is in `artifacts/company-baseline-20261028/` (`a9cdb9e8…`). This review does
**not** extend the DB08 gate (`875fa1a5…`) to these files. It covers them directly.

## Verdict

- **Promotion: REJECT.** The candidate fails the gate declared on 2026-10-27T16:46Z (`m02-predeclared-evaluation.json`):
  - 0 of 3 required eligible gameweeks;
  - goal deviance −0.56% and assist deviance +0.13% (worse), where at least 2% improvement on both is required;
  - the candidate's assist calibration slope is 0.78, outside 0.8–1.2.
  Nabil's recommendation agrees.
- **Keeping the code in the tree, switched off: APPROVE, with conditions.** With the switch off the code reproduces
  the baseline. No caller can turn it on. I found no path where a target match's own outcome enters fitting. Two
  latent filter weaknesses (W1, W2) must be fixed before anyone enables the switch. The conditions are listed at
  the end.
- **Inherited `features.py` change (`stats_ingested_at`): APPROVE.**

## Evidence

**1. What changed and who wrote it.**
- I diffed `features.py` against the r5-gate copy (`artifacts/codex-r5-review-20261026/repo/…`, `6c5af698…`).
  Ignoring line endings, there are two changes:
  - the `player_links` join in `match_frame`. This is FAA-3 work, covered by `docs/research/database-link-audit.md`
    (§ listing `features.py`), which was accepted;
  - `f.ingested_at AS stats_ingested_at` added to `player_gw_frame`. This is the only post-M00 change. It adds one
    column; nothing else selects or removes it.
- `players.py`: the fallback logic and the `xg_source`/`xa_source` columns are inherited (`players-vs-HEAD.diff`).
  Nabil added only the `fpl_xg_fallback=False` gate, the docstring and a formatter reflow
  (`players-nabil-vs-inherited.diff`, verified line by line).
- **Note:** before Nabil's gate, the inherited code ran the fallback whenever `as_of` was passed. No caller passes
  `as_of`, so it was latent then too. The gate is the right fix.

**2. Switch-off behaviour and callers.**
- `fit_player_rates` has one production caller, `project.py:278`. It passes neither `as_of` nor the switch, so the
  fallback cannot run (verified by `grep`).
- Baseline reproduction was verified from the saved artifacts, not re-run: `m02/baseline-reproduced.csv` and M00's
  `baseline-rates.csv` are both SHA-256 `ff69d7b0…13f5`. The source DB hash was the same before and after the run
  (`m02-comparison.json`).
- **Not identical in schema:** with the switch off, the output always has two extra columns, `xg_source` and
  `xa_source` (confirmed by my probe). `project.py:283-284` maps only `source` and `evidence_minutes`, so saved
  projections are unaffected. Byte-identity holds on the original columns only, as Nabil states.

**3. Temporal leakage.** I read the fallback code and the comparison script, and ran two probes.
- **Fitting inputs** (`players.py` fallback block): FPL rows are used only if `stats_ingested_at <= cutoff` **and**
  `kickoff < cutoff`. Detailed-history rows (`pm`) are filtered by the **caller**: `project.py:276` and
  `m02_compare.py:79` both keep only dates strictly before the cutoff's calendar day.
- Links: `player_links.resolve` uses only links with `recorded_at <= decided`. Links recorded on 2026-10-26 were
  therefore not applied at the GW5–7 cutoffs.
- **Reconstruction** (`m02_compare.py:115-122`): `stats_ingested_at = kickoff` is set on an in-memory copy, and the
  fit set is `kickoff < first kickoff of GW k`. `leakage-check.json` shows 0 rows from GW k or later in any fit set.
  The last fitted match kicked off 96 h, 476 h and 112.5 h before the cutoffs, so all were finished. This meets god's
  boundary: ingestion time is relaxed only for completed earlier fixtures. The target fixture's own xG/xA never
  enters fitting.
- **W1 (latent, confirmed by probe).** The fallback's own exposure filter compares `pm.date` (midnight) with the
  cutoff timestamp. Matches on the cutoff day itself therefore pass it. Probe `test_cutoff_day_exposure_enters_fit`:
  raising team xG only in cutoff-day rows changed fitted shares. This is **not an active leak**, because every
  current caller removes cutoff-day rows first, and the position priors and baseline would see such rows too if a
  caller didn't. But the inner filter looks like a safeguard and isn't one.
  Fix: filter on `date < cutoff.normalize()`, or use kickoff timestamps, and add the probe as a test.
- **W2 (latent, confirmed by probe).** `kickoff.isna() | kickoff < cutoff` lets rows with no kickoff through when
  their ingestion stamp is early. Probe `test_nan_kickoff_rows_pass_filter`: 3,060 later rows, re-stamped as ingested
  early and given no kickoff, changed 38 players. Real FPL rows always carry a kickoff, so this needs mislabelled
  data. Fix: fail closed (drop rows with no kickoff).
- Time zones: `pd.Timestamp(as_of)` is compared with naive-UTC ingestion stamps. A timezone-aware `as_of` would raise
  or misalign. State that the input must be naive UTC, or normalise it.

**4. What the GW5–7 numbers are.** They are an **oracle-conditioned share diagnostic**, not forecast or backtest
evidence:
- Scoring multiplies each arm's share by the target match's **realised** minutes and **realised** team xG
  (`m02_compare.py:119, 124-125, 139-142`).
- It conditions on players who played (`minutes > 0`).
- Positions and teams come from the latest snapshot.
- These realised values enter scoring only, equally for both arms, never fitting. That is acceptable for isolating
  the share estimate. It says nothing about expected points, decisions or deployable accuracy, and must never be
  quoted as such.
- The handoff and the JSON label (`EXPLORATORY - reconstruction; 0 eligible gameweeks; not validated; no
  promotion`) say this correctly.

**5. Cohorts.** Counts are internally consistent:
- 890 rows in all;
- 408 goal-fallback rows and 337 assist-fallback rows (these overlap);
- 428 unchanged rows with detailed history in both stats, with deviance change exactly 0;
- at the M00 cutoff, all 344 players with detailed goal history are identical (part B).
No position cohort reaches 50 events, so the regression limit is untestable, as reported.

**6. Tests I ran** (no installs; project `.venv`; temp folder in my scratchpad):
`pytest <scratch>/test_wegz_probe.py tests/test_players.py tests/test_players_fpl_fallback.py tests/test_point_in_time.py tests/test_project.py -q -p no:cacheprovider`
→ **47 passed in 102.8 s**. That is 44 existing tests plus my 3 probes. The probes assert the weaknesses exist, so
they pass. I did not run the full suite or re-run `m02_compare.py`.

## Behaviour if the switch is ever enabled (not reviewed as acceptable)

- `source` changes from `pl`/`translated` to `fpl` for 98 players. `source` flows to `rate_source`, which is read by
  `agent.py:331` (prior count) and `solver/value.py:51`. Those consumers would need checking for the new value.
- It must be saved under a distinct model id (proposed: `sim-v1-fplxg`), never `sim-v1`. Today it can't be produced,
  because `build_projection` doesn't pass the switch.

## Conditions for keeping the code switched off

1. The switch stays `False`. No caller is added without a new review.
2. Fix W1 and W2 and turn my two probes into regression tests before any enablement or new comparison.
3. Label the GW5–7 result everywhere as an oracle-conditioned share diagnostic. It is not forecast, backtest or
   accuracy evidence. No commercial or user-facing use.
4. Next experiment (team denominator; eligible gameweeks from real point-in-time ingestion): declare its gate before
   comparing, as M02 did.

## Limits of this review

- No full suite was run.
- The comparison was not re-executed; I relied on the saved hashes and recorded determinism (1.3e-13).
- No production data was touched.
- The DB08 gate does not cover the other uncommitted files that changed since it (FAA-27 manifest). This review covers
  only `players.py`, `features.py` (`stats_ingested_at`) and the M02 tests and scripts.
