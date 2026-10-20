# Football intelligence and injury policy

Owned doubtful players are eligible to start, including FPL's d/75 flag. Their playing
probability already reduces simulated minutes. Confirmed unavailable statuses and zero
chance remain excluded; purchases retain the stricter available/75+ policy. No player is
hard-coded into a lineup.

For a squad containing a doubt, the first-week lineup receives a bounded swap/bench-order
comparison over the existing simulated worlds. It scores legal auto-subs, zero minutes,
cameos and captain fallback. A cameo prevents substitution; a first substitute can be skipped
to preserve formation. This is a local refinement, not a global optimum. The comparison is
reported before final ownership/captain policy; headline xP still means XI plus captain,
excluding bench cover. Held lineups retain probability-based eligibility but currently use
the existing best-XI fallback rather than this refinement.

## News

```text
.venv/Scripts/python -m fpl_agent.cli news-check --player "Erling Haaland"
```

Omit `--player` to watch the latest season's doubtful players. Repeat `--player` for multiple
names; `--url` accepts an extra article on the explicitly allowed City/BBC hosts. The monitor
checks City's official men's news index and BBC Football RSS, with at most eight linked
articles. HTTPS only, redirects refused, responses capped, request timeouts, no API key.
It appends an evidence ledger to the existing DB; back up an existing DB before first use.

The existing two-hour plan workflow runs this check throughout the week, before the plan's
deadline-window gate. These workflow changes require deployment/push to become active remotely;
this checkout has not deployed them. On a laptop, run the command manually: the dashboard
does not install a background service. Avoid running it while the dashboard has a writer job.

News retains URL, publication date, observation time, source class, matched names and failures.
Modified/crawled dates do not establish publication freshness. Old, future and undated reports
remain labelled. Plans read only evidence observed by decision time and recompute freshness.
No scraped sentence automatically changes injury probabilities: a UCL absence is not proof of
a Premier League absence, and a team-sheet bench place does not prove weekend fitness.

Coverage is currently City official news plus BBC, not every club or every press conference.
HTTP blocks and missing articles are failures/unknowns. Full-name or unique watched-surname
matches are discovery hints, not proof of an injury claim. Source failure does not clear a flag.
The optional paid scout still reads FPL text; these articles are advisory report context.

## Value and differentials

The report watchlist considers players costing at most GBP6.5m, under 10% **observed overall
FPL ownership**, averaging at least 60 projected minutes per GW across up to four weeks.
It ranks expected-point advantage against the median of same-position, similarly reliable
players at the same price or cheaper. It reports price, ownership, projected points, minutes
and rate evidence. Missing ownership is unknown, not zero. Ownership never adds points.

This is candidate discovery, not an automatic transfer or proof of a profitable edge. Check
actual role changes, set pieces, injury-created opportunities and fixture quality before
buying. Prior-only rates are labelled unvalidated. Cup congestion needs complete dated
fixtures and player minutes; no invented rest penalty has been added where those are absent.

Dashboard refresh and the plan workflow now also request ENG1/UCL/UEL/UECL fixtures and up
to 40 missing completed-match pages per competition through the existing resumable importer.
Already imported matches are skipped. An initial refresh can take several minutes; failed
optional sources remain visible. Fixture coverage alone does not establish player workload.

## Historical evaluation

The isolated `data_cache/calibration-study-20261020.duckdb` holds recovered 2024/25 and
2025/26 public FPL archives. `artifacts/minutes-holdout-2025.json` records training on 2024
and testing on 2025, including positional Brier/log loss against recent-starts baseline.
Archive snapshots are reconstructed at kickoff, not verified predeadline injury snapshots;
these results do not validate injury probabilities, transfers or overall FPL returns.
No production model/DB promotion follows automatically from this study.
