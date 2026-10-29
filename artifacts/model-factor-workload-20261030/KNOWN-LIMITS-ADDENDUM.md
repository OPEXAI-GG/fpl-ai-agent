# Known limits addendum (after Wegz r2 PASS, 2026-10-29 22:39 UTC)

These notes add to `CONTRACT.md` "Known limits" and to `comparison-plan.json`
`known_limits_declared_now`.
- **Separate file:** they are kept here so the reviewed r2 hash set in `hashes.sha256` stays
  valid.
- **Plan unchanged:** the plan's rules and minima are unchanged.

## The three notes

1. **Rolled-back repairs are still excluded.** A repair logged after C that was later rolled back
   (`rolled_back_at` set) still drops its rows from `obs_`. This loses rows but never leaks.
2. **Repairs to match rows are not covered.**
   - The exclusion is keyed on `appearances` (`match_id`, `player_id`).
   - A repair that rewrites only the `matches` row, e.g. its matchday or kickoff through
     `_merge_duplicate_matches`, is not covered.
   - This is the same class as the declared limit that `matches.kickoff` is the current value,
     not the value as of C.
3. **The first validated gameweek needs every repair since deploy journalled.** This holds once
   FAA-41 is live, because repairs are off by default and an opted-in repair is journalled.
