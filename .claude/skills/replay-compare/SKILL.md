---
name: replay-compare
description: Score a planner change by replaying a past season on master and on the working tree, then diffing points, hits, transfers and chip timing.
disable-model-invocation: true
argument-hint: "[--season 2025] [--from-gw 1] [--to-gw 38] [--set key=value ...]"
---

# Replay compare

`fpl-agent replay` is the scoreboard for planner tuning: it runs the real planner, chips and rails
through a past season and scores the decisions on official points. This skill runs it twice -
baseline (`master`) and candidate (working tree) - with identical arguments.

Arguments: $ARGUMENTS (passed through to `replay`; default `--season 2025`).

## Steps

1. Check `data_cache/football.duckdb` exists. If not, stop and say the database must be restored
   first (`scripts/data_restore.sh data_cache/export`, needs the `gh` CLI) and built with `db-update`.
2. Create a baseline worktree without touching the user's tree:
   `git worktree add ../fpl-replay-baseline master`
3. Run both, writing per-gameweek CSVs to `data_cache/replay/` (git-ignored):
   - Baseline: from `../fpl-replay-baseline`,
     `uv run fpl-agent replay --db <abs path to repo>/data_cache/football.duckdb $ARGUMENTS --out <repo>/data_cache/replay/baseline.csv`
   - Candidate: from the repo root,
     `uv run fpl-agent replay $ARGUMENTS --out data_cache/replay/candidate.csv`
   Run them one after the other (DuckDB allows one writer at a time). Replays take minutes; use a
   long timeout or run in the background.
4. Remove the worktree: `git worktree remove ../fpl-replay-baseline --force`.
5. Compare the two CSVs with a short pandas script.

## Report

- Total points baseline vs candidate, and the difference.
- Hits taken, transfers made, and chip gameweeks for each.
- The gameweeks with the biggest swings (top 5 by absolute difference) and what decision differed
  there (transfers, captain, chip).
- One line of verdict. A gain of a few points over a season is noise at the default sample count;
  say so, and suggest re-running with more `--samples` before trusting a small difference.
