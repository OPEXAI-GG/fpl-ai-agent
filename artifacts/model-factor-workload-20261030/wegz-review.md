# FAA-49 review: workload factor `minutes-workload-v0` (Wegz, 2026-10-30)

Frozen set: `artifacts/model-factor-workload-20261030/`.
- All 9 manifest entries match (after stripping CR line endings).
- The files: `workload.py` 5d6a0e5b, `checks.py` d6bd67b6, `CONTRACT.md` 660ef1e7,
  `comparison-plan.json` 88f7ef83, `features-2026.csv` 55a5cdc2.

Scope: structural and cutoff review only. No performance approval is possible, because 2026 has
0 eligible gameweeks.

## Verdict: CHANGES NEEDED

Two contract and code items must be fixed, and one plan rule needs clarifying, before this
feature may feed any comparison. Isolation, the cutoff and the window logic otherwise pass.

## What I checked

- **Isolation:** read-only sources. Nothing in `src/` imports it. The candidate id is
  `minutes-workload-v0` / `sim-v1-workload`, never `sim-v1`.
- **Cutoff:** C comes from `eligibility.gameweek` unchanged. The window is
  `[C-30d, C-3h]` on kickoff. The population is the one observed pull that eligibility uses.
- **Reproduced:** `checks.py --gw 8` passes all 5 checks for me (read-only export).
- **Independent queries on the r20261028 export:**
  - European club ids;
  - GW8 linked players whose 30-day minutes are all for another club.

## Required

**C1. Wrongly linked players are scored as "rested" (0 minutes), not unknown.**
- In GW8, **63 linked players** have 30-day minutes only for a club other than their FPL club,
  and none for their own club. Only 2 have both.
- Mid-season there is no transfer window, so these are name-link namesakes. Examples from the
  European rows: Inter rows on a Liverpool player, Juventus on Newcastle, Benfica on Man City.
- The same-club filter drops the foreign rows. The player then gets counts of 0,
  `rest_days` NULL and `no_recent_30d` = True. That is "did not play" for a player whose real
  appearances are not linked at all.
- This breaks the contract's own rule, "unknown is not rest", for about 12% of linked players.
- **Fix:** a linked player whose 30-day window has other-club rows and no same-club rows gets
  the unlinked treatment: all NULL, plus a flag `link_suspect`.
- Add a check and a cohort for it in the plan.

**C2. "obs_ is lossy, never leaky" is not true under the existing repairs.**
- `ingested_at` changes only on INSERT OR REPLACE.
- Master's automatic repairs rewrite rows in place with UPDATE and keep the old `ingested_at`:
  - `merge_split_teams` rewrites `team_id` (`quality.py:293` at 51f0def);
  - `_merge_duplicate_matches` rewrites `match_id` (`quality.py:328-330`).
- So a row observed before C can have its club or match changed after C. It can then enter
  `obs_` through the same-club test, or move window through the merged match's kickoff, using
  post-cutoff knowledge.
- The production export carries no record of those repairs, because master applies them
  unjournalled.
- **Fix:**
  - in the contract, state `obs_` as "as of last insert, excluding in-place repairs";
  - for any eligible run, exclude rows whose match, team or player was touched by a
    `repair_log` entry after C. FAA-41 journals every repair, and repairs are off by default;
  - or require proof that no repair ran after C.
- Same class of issue, already declared: `matches.kickoff` is the current value. Accepted as
  declared, with a check for kickoffs changed after C once repair or ingest history allows it.

**P1. The comparison plan's training rule makes a validated result ambiguous.**
- The plan trains on earlier gameweeks with workload computed by `workload.py`. Past seasons'
  workload can only be `rec_` (reconstruction), and the plan then calls the run "exploratory".
- Training on `rec_` and evaluating on `obs_` is also a train/serve skew.
- **State one of these in advance:**
  - (a) train the extra inputs on `obs_` from eligible gameweeks only, which needs many more
    than 3 eligible gameweeks; or
  - (b) train on `rec_`, evaluate on `obs_` for eligible gameweeks, and say explicitly whether
    that result can count as validated.
- Without this, the "earliest possible run" (about GW12) is not reachable as a validated claim.

## Answers to Nabil's four questions

1. **Leakage:** event-time logic is correct. The 3 h buffer, the target week and
   post-cutoff events are excluded, and check 1 confirms it. `obs_` is not strictly leak-free
   because of C2. The kickoff limitation is declared and acceptable as a known limit.
2. **Identity:**
   - The same-club rule is sound as a filter. Every mismatched European row is a different,
     real club (no split club ids), so European minutes are not undercounted by it.
   - What follows is not sound: see C1.
   - Same-club namesakes remain undetectable, as declared.
3. **Checks:** GW6-8 results are reproduced for GW8. The GW6 skip of check 2 is justified by the
   break; GW7 and GW8 cover that path.
4. **Plan minima and promotion rule:** acceptable in form.
   - The minima: 3 eligible GWs, 1,500 rows, 150 European rows, 300 starts and 100 non-starts.
   - The promotion rule: 2% on both log losses, calibration 0.8-1.2, at most 1% regression in
     cohorts of 300 rows or more, plus leave-one-gameweek-out.
   - Add the `link_suspect` cohort from C1.
   - Give the "unlinked must not be worse" cohort the same 1% threshold explicitly.
   - Resolve P1.

## Not approved

Any performance claim, wiring, promotion or tuning: there are 0 eligible gameweeks. No merge.
