# FPL Agent: corrective work and local dashboard

## Subscription product research and release plan (2026-10-21)

**Objective:** turn the local agent into reliable, professional decision support for arbitrary
managers, with a commercially usable data path and a testable recurring-subscription proposition.
This section is the current handoff. Earlier approvals below cover their historical local scope;
they are **not approval for paid/public release or proof of forecasting superiority**.

**Plan:** (1) audit code, live UI and current primary sources; (2) reproduce important defects
offline; (3) specify prediction/evaluation repairs; (4) define customer isolation, product design,
rights and economics; (5) execute the prioritized tickets below in dependency order.

**Research acceptance:** findings cite actual code or sources; major claims have reproducible
evidence; market prices distinguish promotions/unknowns; forecast skill is separate from software
correctness; tasks have testable gates; no production model, account, secrets or deployment changes.

**Progress:** research and planning complete for this pass. Exa: 19 searches, 95 requested result
slots, 93 distinct candidate URLs, 22 distinct URLs attempted for deeper page retrieval. These are
not counts of independently verified studies. Primary sources, caveats and excluded results are
recorded. Actual loaded desktop/mobile screenshots opened and inspected. Core planner accepts
generic squads, but persisted decisions/preferences/lessons are not isolated by customer.

Deliverables:
- [Model reliability audit and experiment protocol](docs/research/model-reliability-2026-10-21.md).
- [Product, design, licensing and subscription strategy](docs/research/subscription-product-strategy-2026-10-21.md).
- [Research source manifest](exa-results/fpl-product-strategy-2026-10-21/source-manifest.json).
- [Offline probe script](artifacts/product-model-audit-20261021.py) and [results](artifacts/product-model-audit-20261021.json).
- [Loaded desktop](artifacts/product-audit-overview-desktop-loaded.png) and [loaded mobile](artifacts/product-audit-team-mobile-loaded.png) audit screenshots.

**Verification:** `.venv/Scripts/python.exe artifacts/product-model-audit-20261021.py` exited 0.
Synthetic 100,000-world probe: 40.944% have >10 outfield starters, maximum 19; two near-zero-start
GKs become approximately 50/50 starters; a zero-gain manual transfer emits `act_now`; an unpaired
comparison reports a win although common-row RMSE loses 2.0 vs 1.8; zero-minute yellow-card scoring
returns zero. These demonstrate defects, **not new real-team accuracy results or fixes**. Browser
console reported zero errors/warnings on inspected routes. Full production test suite was not
rerun for this research-only pass; prior results below must not be represented as current model
validation. Production model code and DB were left unchanged.

Read-only latest-projection audit: 277 PL / 27 translated / 364 prior rate sources (668 players).
Reviews and lessons both zero: the reactive-memory defect is latent, not a demonstrated cause of
the latest recommendation. Saved `artifacts/product-data-audit-20261021.json`.
Final research checks: audit script Ruff lint/format passed; probes reran successfully; report
local links and three strict-JSON artifacts validated; scoped Git whitespace check passed.

**Verdict: Changes needed before commercial release.** C01–C13 are proposed implementation work,
not completed work. A10 remains partial and now has a more specific validation path. A12's hosted
scheduler is optional locally but becomes part of C11 for a continuously available paid service.
No paid services, outreach, deployment or FPL execution were performed.

### [ ] C01 | P0 | Make evaluation comparisons trustworthy

- Files: `model/backtest.py`, `review.py`, `model/features.py`, `model/calibration.py`, README accuracy claims.
- Steps: pair model/baseline rows by identity and cutoff; persist exact official deadlines and known-at timestamps; exclude post-deadline fallback projections and incomplete outcomes from learning; audit latest-snapshot feature joins; freeze E0–E5 protocol from the model report. Preserve historical diagnostic outputs separately.
- Accept: synthetic unequal-coverage example correctly reports loss; adding later snapshots/news/odds cannot change earlier eligible forecasts; incomplete results are not zeros; every metric includes common-row n, coverage/exclusions, data/model/rules IDs and cutoff; README claims trace to reproducible eligible artifacts or are explicitly unverified. No claimed holdout improvement without fresh output.
- Depends: none; prerequisite for model tuning and accuracy marketing.

### [ ] C02 | P0 | Repair simulation and participation semantics

- Files: `model/minutes.py`, `model/simulate.py`, `model/project.py`, `rules/engine.py`, related simulation/rules tests.
- Steps: define availability/start/cameo probabilities; build coherent club lineup/minutes scenarios with explicit unknown replacements; respect unavailable players, goalkeeper coverage, substitutions and scoring participation; validate probability calibration after joint conditioning.
- Accept: all audit counterexamples have regression coverage; valid scenario starting counts/roles and minutes accounting; unavailable GK cannot be selected through normalization; missing coverage produces an explicit degraded state; card-only participation and scoring match official rules. Owned d/75 remains start-eligible, including legal cover/cameo scenarios, with no player-name exception. Publish final sampled marginals, not only classifier scores.
- Depends: C01 protocol; preserve current dry/account safety rails.

### [ ] C03 | P0 | Replace reactive policy learning with validated policy versions

- Files: `lessons.py`, `solver/decide.py`, `solver/rank_target.py`, `solver/cover.py`, `solver/chips.py`, `agent.py`.
- Steps: keep outcome review descriptive; prevent a lucky or break-even one-week result automatically lowering decision thresholds; preserve current policy as an experiment; add neutral expected-points default and explicit user objectives; remove implicit top-10k chasing/default hard ownership locks from the proposed customer policy. Rerank final plans consistently after captain/bench choices.
- Accept: zero-gain manual move does not change production policy; no retrospective tuning without a versioned validated experiment; identical state/model/policy produces reproducible advice; neutral policy does not silently chase a rank; final transfer/chip/hold comparisons use identical scoring semantics and expose expected cost/downside. Held-lineup path receives equivalent availability/cover evaluation.
- Depends: C01/C02; pair policy trials before promoting a replacement.

### [ ] C04 | P0 | Resolve commercial data and branding rights

- Scope: existing FPL/FotMob/archive/news/odds sources, candidate licensed suppliers, fonts/images/branding, export formats.
- Steps: build per-field provenance/rights matrix; establish permission or replacement for restricted feeds; obtain use-case-specific coverage/price/rights responses before selecting a supplier. Include model training, derived forecasts, retained history, publication, caching, exports and territory. Check product-name/image rights separately.
- Accept: each launch feature has a documented permitted source and renewal cost; no assumed right to resell raw feeds; required fields pass a representative coverage/correction audit; legal questions have a reviewed resolution. Missing permission blocks affected commercial use, not silently substitutes a scraper. No purchasing or external messages solely on this ticket's authority.
- Depends: none; run discovery alongside technical repair. FotMob terms are a documented blocker, not a completed procurement decision.

### [ ] C05 | P0 | Version and confirm any manager's actual state

- Files: `data/entry.py`, `execution/live_state.py`, API contracts and future onboarding UI.
- Steps: import arbitrary entry last-deadline state with provenance; let user confirm/correct current squad, bank, buy/sell prices, FT and chips; persist a state version. Explicitly handle GW1, late entrants, Free Hit reversion, active chips, missing histories and already-made transfers.
- Accept: representative valid/invalid states and at least 20 diverse consented/synthetic profiles pass reconstruction and legal-plan checks; inferred fields visibly require confirmation when material; no password required for initial value; public entry ID is not treated as proof of ownership; one profile never changes another's inputs. Test legal budgets and selling-price rounding. No fabricated current squad.
- Depends: C01 rules/cutoff contract; full private persistence depends on C06.

### [ ] C06 | P0 | Isolate customers across storage, jobs and learning

- Files: `db/schema.py`, `review.py`, `lessons.py`, `dashboard/service.py`, `dashboard/jobs.py`; new authenticated API/persistence boundary.
- Steps: separate shared football forecasts from private account/team/state/plan/job data; scope review history and preferences; preserve versioned decisions; enforce verified identity and authorization at every boundary. Define migration/backups for existing local records before executing any migration.
- Accept: tenant A cannot read/mutate/cache-hit/export/cancel tenant B's work; retries and concurrent requests retain correct context; no global latest-plan leak or cross-manager learning contamination; restore/deletion procedures tested. Automated negative authorization tests cover APIs, workers, cache keys and artifacts. Public service does not expose the local loopback control server.
- Depends: C05 contract; required before a hosted multi-user beta. Migration execution remains a separate reviewable action.

### [ ] C07 | P1 | Evidence-aware forecasts and value discovery

- Files: `model/players.py`, `model/translation.py`, `model/project.py`, `solver/value.py`, `solver/inputs.py`.
- Steps: per-component coverage/exposure metadata; calibrated role/minutes distributions; direct-points challenger using allowed inputs; separate penalty/non-penalty evidence; validated assist/DefCon/bonus treatment; squad-specific gain/uncertainty for value picks. Keep uncertain/prior-only candidates in a labelled research tier.
- Accept: all high-confidence recommendations have auditable evidence; missing stats never become observed zeros; no invented ownership or low-ownership points bonus; prior-only targets cannot bypass recommendation quality gates; E1/E2/E5 paired results and source-quality cohorts published. Promote only against the predeclared gate, including inconclusive outcomes.
- Depends: C01/C02/C04. Reuses remaining A10 work; do not duplicate it as a separate implementation.

### [ ] C08 | P1 | All-club news and actual cross-competition workload

- Files: `news.py`, `model/features.py`, ingestion/identity modules, scheduler and evidence UI.
- Steps: source registry for all clubs; structured dated claims and conflicts; monitor healthy as well as flagged players; deduplicate syndication; ingest actual cup minutes/recovery; bounded deadline-relative refresh. Record quotation permissions and machine-extraction confidence. No LLM-generated fitness probabilities.
- Accept: all 20 clubs have declared coverage or a visible gap; an undated, old, wrong-player or wrong-fixture article cannot clear a doubt; failed requests visible; a newly injured unflagged player can be detected; training return does not imply starting. Replay contradictory press conferences and European cameo cases; C01 prevents future-news leakage. E4 must demonstrate benefit before workload/news adjustments change production probabilities.
- Depends: C01/C04; no claim that current City/BBC monitoring already satisfies this ticket.

### [ ] C09 | P1 | Design and validate the customer weekly workflow

- Scope: customer UI only; use the strategy's desktop/mobile critique and design brief.
- Steps: prototype This Week/Squad/Discover/Plan, team confirmation, clear hold/transfer alternatives, readable pitch/list, player evidence sheets and changed-since-last-plan states. Separate restricted operations UI. Choose stack after confirming component/API boundaries; no framework rewrite solely for appearance.
- Accept: inspect actual loaded 360/390/768/1440px screenshots; no truncated identity essential to choosing a player; accessible list equivalent; measured contrast/focus/tap targets; honest loading/stale/unknown states. In a five-user usability round, at least four identify action, risk and freshness within 30 seconds and compare an alternative unassisted. Capture failures and iterate; do not mark complete from a build alone.
- Depends: C05/C06 contracts; prototype work can begin before implementation completes.

### [ ] C10 | P1 | Run a fair multi-team prospective evaluation

- Scope: E0–E5 protocol, immutable forecast/plan logs and public methodology/performance page.
- Steps: reserve untouched evaluation period; compare neutral/current/challenger policies from identical initial states; include rolling, hits, legal autosubs, chip windows, no-shows and missing-data abstentions. Capture future forecasts before deadlines for 8–12 initial GWs and extend if underpowered.
- Accept: paired metrics, block uncertainty intervals, cohort coverage, exclusions and costs available; all selected value picks retained in evaluation; correlated squads are not treated as independent seasons. No launch accuracy claim exceeds evidence. No threshold retuned on test outcomes. Source rights allow any external benchmark comparison and publication.
- Depends: C01–C03/C07; prospective logging can start earlier with explicit experimental status.

### [ ] C11 | P1 | Operate reliably near deadlines at bounded cost

- Scope: shared forecast jobs, private solve jobs, hosting/admin boundary, logs and scheduler (extends A12 for SaaS).
- Steps: profile stages; cache shared forecasts; dedupe/version private solves; quotas, worker limits, retries, cancellation, graceful timeouts and source-outage behavior; monitor missed deadlines and stale advice; backups/restore drill.
- Accept: publish workload/hardware and observed p50/p95 latency/cost; target cached brief <2s and ordinary solve <30s p95 at declared load, otherwise revise capacity before promising it; deadline burst test meets queue SLO. Missing update never appears fresh. No laptop dependency for advertised continuous monitoring. Authentication, secret isolation and dependency checks pass. Deployment requires a concrete reviewed release, not just a bind-address change.
- Depends: C04/C06 and measured engine costs.

### [ ] C12 | P1 | Validate demand and implement honest recurring billing

- Scope: 10–15 customer interviews, 20–30-user beta, pricing experiment, billing/entitlements.
- Steps: test weekly decision-service positioning; record consent and interview evidence; test £6.99/month and £49/year as hypotheses; recompute economics with actual licenses/compute/tax/processor costs. Use hosted checkout/portal, verified idempotent webhooks and entitlement reconciliation. Review jurisdiction-specific renewal/privacy/consumer terms before charging.
- Accept: proposed directional gates 70% import completion, 50% four-deadline return, at least 10 explicit willingness-to-pay commitments; document denominators/limitations. Billing handles duplicate/out-of-order events, failed payments, cancellation, refunds and account deletion. Checkout return URL alone grants nothing. Positive contribution after actual costs; no rank guarantee or claim of validated demand from competitor prices.
- Depends: C04/C06/C09/C11; no unsolicited outreach, purchases or publication authorized by the written plan.

### [ ] C13 | P0 launch gate | Independent release review

- Review C01–C12 evidence against the limited product being sold; explicitly classify experimental features.
- Accept: data rights resolved; no cross-customer access; legal recommendations from confirmed states; P0 defects closed with regressions; no false accuracy claims; real usability/browser checks; backup/restore and deadline behavior verified; billing/support/cancellation usable. Record Approved or Changes needed with specific remaining blockers.
- A paid decision-support product may ship without claiming superior predictions if its stated functionality is validated; an accuracy superiority claim requires C10's separate evidence. Do not confuse those gates.

## Injury-aware decisions and differential discovery (2026-10-20)

Objective: distinguish doubtful owned starters from confirmed absences, verify dated football
news, and surface affordable differentials using evidence rather than a low-ownership bonus.

Plan: (1) separate buying/starting eligibility; (2) test zero-minute cover versus cameos and
legal formations; (3) add bounded, dated source monitoring with honest coverage/failure states;
(4) report value/differential candidates and evidence quality; (5) rerun local dry analysis.

Acceptance: an owned d/75 player can start/captain; confirmed unavailable players cannot;
buying remains conservative; stale news cannot clear an injury; no invented ownership or
player-specific rate confidence; source URLs and timestamps are reviewable; no FPL writes.

Progress: eligibility correction, simulated cover refinement, advisory source ledger and value
watchlist implemented. Historical archives recovered into an isolated study: 27,605 training
rows (2024), 29,747 holdout rows (2025). Start Brier 0.07826 vs baseline 0.10358; log loss
0.25332 vs 0.35081; model beats baseline in each position. See
artifacts/minutes-holdout-2025.json for all positional metrics and reconstruction caveats.
Detailed source probe succeeded; backed-up production enrichment completed: 6,862 appearances,
101,072 player-stat rows, 70 ENG1 / 38 UCL / 36 UEL / 18 UECL completed matches; quality clean.
Future dashboard/workflow refreshes now request missing details through the same importer.
A12 is a documented deployment deferral, not an unfinished dashboard feature; existing
Actions schedules cannot be described as an independent external scheduler.

Verification: full `pytest -q -p no:cacheprovider --cov --cov-report=term --cov-fail-under=85`:
**658 passed, 6 skipped, 13 deselected; coverage 91.23%; 1281.29s**. Focused injury/cover/news/
value/decision/execution/rules: **97 passed**. Agent/projection/point-in-time/workflow:
**96 passed, 1 skipped**. After late refresh/health refinements: controls/workflows **56 passed,
1 skipped**; dashboard/news/controls **38 passed**; final filtered dashboard/news **27 passed**.
Ruff check and format passed (142 files), JS syntax and scoped Git whitespace check passed.
Final wheel built and checked for new intelligence modules/assets and filtered news service.
Browser suite: **6 passed**; Windows cleanup required terminating only its isolated fixture
server. Final real desktop/mobile screenshots were opened and inspected; no page overflow or
browser errors. Files: artifacts/dashboard-enriched-team-{desktop,mobile}.png and
artifacts/dashboard-news-health.png. The localhost dashboard was restarted with final code.

Final enriched-data dry run: job `4fdcb72c3272`, run `ebf7b3d8409f4255`, projection
`aa41fa953903`; exit 0, ok/planned, no blocks. Roll FT, no chip, 3-5-2, Haaland starts;
captain Calafiori, vice Raya; bench Truffert / Thomas-Asante / Tarkowski, goalkeeper Leno.
49.524 predicted XI-plus-captain points, GBP0.2m bank. Analysis and caveats:
artifacts/gw8-injury-aware-analysis.md; raw log: artifacts/enriched-injury-aware-dry-plan.txt.
Latest news check reached BBC; City automated HTTP requests failed and remain visible.
Separate browser research verified the official City press conference/UCL team sheet.
No automatic fitness clearance, FPL writes, notifications, remote deployment or secrets edits.

Verdict: **Approved for the implemented local injury/value/news scope.** Broader club/press
conference coverage, calibrated assist/DefCon predictions, paired policy evaluation and an
independent hosted scheduler are not complete. A10 stays partial; A12 stays explicitly
deferred. These statuses preserve actual limitations rather than claiming all model work done.

## Premium dashboard implementation (2026-10-20)

Objective: refine the existing local Python dashboard into a cohesive dark football analytics interface, preserving safe controls and real data.

Plan: (1) inspect existing API/UI and data contracts; (2) implement responsive design, overview, interactive pitch and player analytics; (3) verify real and empty states in a browser; (4) run relevant checks and document results.

Acceptance criteria: one-command localhost startup; eight working routes; desktop/mobile layouts; real metrics and explicit unavailable states; accessible interactions and charts; unchanged credential isolation and dry controls; dashboard tests and browser checks pass.

Progress: complete. Root AGENTS.md and CLAUDE.md absent; supplied instructions apply. Existing unrelated working-tree changes preserved. Reused Python WSGI/DOM stack without new dependencies, frontend compilation, external fonts or CDNs.

Implemented: eight responsive dark Matchroom pages; real points-leader charts and fixtures; interactive pitch, captain/vice and bench; keyboard-accessible player details with official form/recent gameweek history; comparisons and search/filter/paging; club/gameweek calendar filters; persistent agent run history; safe existing refresh/dry-plan/review controls. Unknown account statistics, exact deadlines, difficulty ratings and narrative reasoning are explicitly unavailable. Predictions labelled. Backend additions are read-only stored-data queries (service.py:277, service.py:396); freshness advances without a DB write (server.py:127); kill switch uses the existing config parser (service.py:471).

Visual QA found and fixed: Unicode icon encoding, focus scrolling, dropdown accessible names, dry-plan button remaining disabled after settings save, delayed search rerender after navigation, and pitch grid clipping a five-player row at 320px. CSP retained; dynamic chart sizing uses DOM style properties. No FPL account changes or production data mutations performed.

Verification (2026-10-20):
- `.venv/Scripts/python -m pytest -q tests/test_dashboard.py tests/test_dashboard_controls.py -p no:cacheprovider`: **30 passed in 10.22s**.
- `node node_modules/@playwright/test/cli.js test -c playwright.dashboard.config.ts`: **6 passed in 7.2s**. All eight routes at 1440/390/320px; pitch geometry, XI/bench/captain, modal keyboard/focus, search/position/paging/compare, calendar filters, settings/confirmation, stale/missing/error states and injection safety.
- `ruff check` and `ruff format --check` on dashboard and relevant Python tests: passed (7 files formatted).
- `node --check src/fpl_agent/dashboard/static/app.js`, Python compileall and scoped `git diff --check`: passed.
- Real-data Chromium console: **0 errors, 0 warnings**. Actual screenshots opened and visually inspected: artifacts/dashboard-desktop.png, dashboard-mobile.png, dashboard-team-fixture.png, dashboard-team-mobile-fixture.png. Team screenshots use isolated, synthetic test decisions; overview screenshots use the real local DB.
- No frontend build step exists. No new dependencies required for runtime. Full agent/Discord suites not rerun because unrelated code was preserved; checks above cover this dashboard work.

Deliverables: packaged dashboard assets and read APIs; docs/dashboard.md with one-command launch, data limits and checks; `npm run test:dashboard` with isolated temporary fixture DB. Run `.venv/Scripts/python -m fpl_agent.cli dashboard --port 8765`, then open http://127.0.0.1:8765.

Review verdict: **Approved for this dashboard scope.** Live account stats/current squad remain unavailable; real-data proposed XI requires the owner's team ID and a dry plan. Backend corrective audit tasks below retain their separate status.

## Final integration and next-gameweek analysis

Objective: independently verify Claude's changes, finish evidence-supported A08/A10/A11 work and run a forced dry plan for saved entry 8700026. No live account execution, deployment, historical identity repair or parameter tuning without usable evidence.

Plan: verify full gate; repair missing-stat exposure and cross-workflow auth race; surface run reasons/scout usage; perform advisory/frozen-install/wheel checks; record profiling/evaluation readiness; refresh backed-up data; run and inspect the next-week dry plan; reconcile task status honestly.

Progress: initial full offline gate passed 626 tests (6 skipped, 13 deselected), coverage 90.54%, in 666.81s. This run began before the final new regressions were collected; a final gate will follow. Targeted new-code suite: 125 passed, 1 skipped. Discord: 14/14 passed. Frozen `uv sync --frozen` succeeded in data_cache/frozen-verification-env (38 exact packages); working .venv untouched. Wheel built. Python installed-package advisory scan: no known vulnerabilities. npm audit: 7 high findings via unused shadcn CLI; removing only that devDependency and its unused lock graph reduced audit to zero. No forced upgrades.

Data readiness before refresh: 668 players, 3,883 points rows (2026 GW1-6), zero detailed appearances/player stats, zero historical rows with a matching GW position snapshot. Saved artifacts/data-readiness.json. No honest historical holdout/calibration result is possible on these inputs; do not claim fitted-model improvements. Backed up DB to data_cache/backups/football-before-final-analysis-20261020-180844.duckdb. Public refresh succeeded (GW7 now loaded; quality no issues); optional football-data source connection failed and degraded explicitly.

Implementation: unknown FotMob defensive fields remain unknown; assist/xG/defensive priors use only observed exposure. Plan and token-check share fpl-auth job lease; before OAuth, queued runs compare token updated_at to workflow created_at and fail closed on stale/missing metadata. Auth/model keys scoped to needed workflow steps. No deployed workflow or secret was changed. Run reasons/stages and sanitized scout-call summaries now available in dashboard.

Verification: subsequent full gate passed **639 tests, 5 skipped, 13 deselected; 90.24% coverage in 684.06s**. The last availability regression was added after that run collected tests; its targeted solver/execution/data suite passed **103 tests in 92.43s**, and the specific regression passed separately. Ruff check/format: all passed (133 files); JS syntax passed; final wheel loaded rules, start-eligibility field and static assets outside the repo. Frozen install and advisories are recorded above. Additional targeted checks: 87 passed, 1 skipped; browser suite 6 passed in 8.9s. Real-profile artifact model-thread-profile.json: 4,551 official fixture rows, three paired fits; median 32-thread fit 1.0103s vs 2-thread 1.0286s, equal probabilities. No benefit; no thread-default or fit-cache change retained. Ownership budget now checks standings requests too; Retry-After accepts bounded dates/deltas.

The first real dry plan (504fe7f531fa) completed, but its lineup was blocked by availability_start for an owned doubtful player. This exposed a planner/rail mismatch: owned players bypassed candidate buyability filters and remained eligible to start. Fixed with per-week candidate start eligibility in ordinary/Free Hit solves and availability-aware held/no-transfer lineups; regression with a 100-xP doubtful owned forward passes. Safety rails unchanged. Initial report preserved in artifacts/next-gameweek-dry-plan-initial.txt; corrected analysis completed as run 076ae053a91d4da0 / job f7b4b00f726d. Entry 8700026, GW8, mode dry, status ok, state planned, exit 0. Outcome has no blocks/errors and only a "would set captain" action. Proposed 3-5-2, roll the transfer, no chip, stored xP 46.450, captain Evanilson, vice Calafiori; Haaland benched under current availability rules. Model 6.494s; decision/chip scenarios 371.508s; execute 0.003s. No authenticated request or account write was enabled. Saved artifacts/next-gameweek-dry-plan.txt and data_cache/dashboard/plans/gw8.{md,json}; verified live local browser has 11 starters and 4 bench with no console errors. Screenshot artifacts/my-team-gw8-dry.png inspected. The first child exited 0; its parent printing initially hit Windows CP1252 on a player name, so the persisted output was recovered as UTF-8 without re-executing actions.

## Objective and project summary

Deliver trustworthy data and execution, measurable runtime/API/AI savings, and a local dashboard. Preserve the CLI, deterministic simulation, planner, and dry defaults. Implementation status per task: see the `[ ]`/`[x]`/`[!]` marker on each heading and the Claude completion record below.

Verified checkout: Python >=3.12, local venv Python 3.13.12; httpx/Pydantic clients; pandas/NumPy features; DuckDB plus parquet release persistence; gradient-boosted minutes, team-strength and player-rate models; Monte Carlo projections; SciPy/HiGHS multiweek MILP and chip scenarios; safety/execution; review/calibration/lessons; GitHub Actions; a separate Node/Cloudflare Discord bot. AI is an optional bounded injury scout and Discord Q&A, not the transfer optimizer. `RunOptions.horizon` is 8 (`src/fpl_agent/agent.py:42`), despite six-week documentation. No local web UI/server or web framework dependency exists (`pyproject.toml`, `cli.py:392`).

Strengths worth retaining: dry execution returns before authentication; source transactions, contracts, quality checks, fixed simulation seeds, candidate pruning, offline fixtures/synthetic/property tests, and an 85% CI coverage gate. Weaknesses: critical failures can return success; DGW statistics are duplicated; live POST/read-back assumptions are unsafe; snapshot age is unchecked; historical evaluation can see later information; identity linking is name-based; execution/delivery history is incomplete.

### Evidence and companion audit reconciliation

Audit date: 2026-10-20. Root `AGENTS.md` and `CLAUDE.md` were absent; supplied AGENTS instructions govern. Git, uv and Node were unavailable on PATH; existing venv tools work. No secrets/config values were read or displayed. No live API/authentication, production inspection, dependency installation, or vulnerability database scan was performed.

Read the supplied `FPL Input Flow Audit (Copy).md`: overview, stage analysis, all 112 finding summaries/fixes and relevant detailed evidence. It describes fixed revision `436aa86` and production `b3cacf0`, not a verified revision of this checkout. Its production counts, timings, test results and live-account claims are **external evidence, not independently reproduced here**. In particular, its reported F002 incidents observed binding preview POSTs during Wildcard weeks; ordinary-week production behavior was not observed. Do not repeat authenticated writes to investigate.

The companion's `identity.py`, `release.py`, `prodconfig`, `limits.py`, `match_odds_history`, scout ledger, cold-start/cover additions, six-hour freshness rail and fuller read-back are absent here. Do not transplant its line numbers or prescribe removal of absent code. Its F003 dated-archive preference bug is in absent release code; this checkout instead has the simpler fail-open restore below. F010/F024/G1.1 cover/blend regressions and F048 expensive translation validation do not apply here. The audited production claim of zero scout adjustments is not a measured result for this checkout. F082 is refuted for that audit's production workflow; do not turn it into a production bug here. Notification failure retry remains a separate verified gap.

Labels below: **V** = verified in current code; **R** = reproduced by an offline probe; **E** = companion report requiring deployment/data verification; **O** = proposed optimization requiring measurement. References are current checkout lines unless explicitly identified as companion IDs.

## Prioritized tasks

Existing paths below are relative to repository root; paths explicitly marked **new** are proposed. Run targeted tests after each task; use the final gates below at phase completion.

### [x] A01 | P0 | Make critical ingestion fail closed

Paths: `src/fpl_agent/pipeline.py`, `health.py`, `contracts.py`, `tests/test_pipeline.py`, `tests/test_contracts.py`.

- **V/R:** `pipeline.py:96` marks player loading critical but ignores its result; final code at :183 depends only on quality issues. Injecting `FootballDB.team_id` failure yielded **exit 0, critical_failed=True**. Fixtures at :104 are optional even though decisions require them. `repair(db)` at :78 runs before critical bootstrap retrieval, contradicting the documented no-write-on-FPL-failure guarantee. Contracts commonly inspect only the first row (`contracts.py:20-59`).
- Steps: validate all required FPL rows before mutation; stop after any critical failure; classify current-season fixture retrieval as critical for refresh/planning; defer repair until critical input validation succeeds. Preserve existing valid state on failed critical load, with a tested transaction boundary. Preserve optional-source degradation. Distinguish empty future/unstarted live data from an invalid empty finished-GW feed.
- Depends: none; execute first.
- Accept: bootstrap/player/fixture failure returns 2, never exports, never permits a plan on that failed refresh; malformed non-first row is caught; optional Understat failure still degrades. Pre-bootstrap failure leaves tables unchanged, including repair candidates.
- Verify: `uv run pytest -q tests/test_pipeline.py tests/test_contracts.py tests/test_quality.py`.

### [x] A02 | P0 | Replace the assumed preview protocol and verify complete outcomes

Paths: `src/fpl_agent/execution/{execute,fpl_writer,live_state,safety}.py`, `src/fpl_agent/agent.py`, `tests/test_execution.py`, `tests/test_live_state.py`, `tests/test_agent.py`.

- **V:** `execute.py:126-134` sends two transfer POSTs; empty preview skips comparison. `fpl_writer.py:86` compares incoming players but not complete outgoing pairs/prices/chip. Read-back at `execute.py:151` checks only captain, and idempotency at :120 only tests incoming membership. `tests/test_execution.py:348` explicitly approves empty preview. **E:** companion F002 reports the first POST actually applied transfers, then the second failed and lineup was skipped.
- Steps: model transfer submission as a potentially binding operation, never a speculative preview. Preflight against the freshly authenticated squad, selling/current prices, bank, FT/hit cost, chip and rails. Use one validated submission; pin its payload to sanitized captured evidence supplied by the owner, documenting any unknown contract fields. After success or ambiguous failure, reconcile my-team before deciding whether anything may be retried; never blindly resubmit a write. Resume lineup after an already applied transfer. Compare exact squad, XI, bench slots, captain, vice, active chip, bank and transfer cost where exposed; report unavailable verification explicitly. Only persist `live-done` for completed, verified actions; held runs must never acquire that status (`agent.py:272` currently permits it).
- Depends: A01; can develop offline alongside A03.
- Accept: mock first POST applies swaps and second POST would fail; exactly one POST occurs and lineup completes. Cover lost response, 4xx after application, unchanged/mismatched read-back, wrong outgoing pair, partial squad, wrong bank/cost and repeated run. No false verified/live-done result. All existing dry/kill-switch zero-auth guarantees remain.
- Verify: `uv run pytest -q tests/test_execution.py tests/test_live_state.py tests/test_agent.py`. Use mocks only; live validation is a separately authorized rollout, not an acceptance shortcut.

### [x] A03 | P0 | Preserve active chip state across replans

Paths: `src/fpl_agent/data/entry.py`, `execution/live_state.py`, `execution/fpl_writer.py`, `execution/safety.py`, `solver/decide.py`, `agent.py`, `tests/test_live_state.py`, `tests/test_decide.py`, `tests/test_execution.py`.

- **V:** `live_state.py:59-87` records all active/played chips as used but only models active Wildcard; `SquadState` has only `wildcard_active`. Subsequent BB/TC/FH runs can lose chip context or classify FH changes as human edits. Companion F015/G5.4 corroborate the risk; production impact remains external.
- Steps: represent active chip and its event explicitly; carry BB/TC/FH through decision, payload, rails, report and read-back; prohibit a second chip; keep FH temporary squad separate from base squad and human changes. Preserve existing WC behavior unless a captured contract justifies changing it.
- Depends: A02 outcome contract.
- Accept: repeated runs for each chip preserve chip and legal squad, do not activate twice, do not mislabel bot FH swaps as hand edits, and revert FH next GW. Tests include BB/TC already active with unchanged squad.
- Verify: `uv run pytest -q tests/test_entry.py tests/test_live_state.py tests/test_decide.py tests/test_execution.py tests/test_agent.py`.

### [x] A04 | P0 | Correct fixture-level official history

Paths: `src/fpl_agent/db/ingest_fpl.py`, `model/features.py`, `db/quality.py`, `pipeline.py`, `.github/workflows/plan.yml`, `tests/test_db.py`, `tests/test_pipeline.py`, `tests/test_minutes.py`, `tests/test_quality.py`.

- **V/R:** `ingest_fpl.py:116-134` copies GW aggregate minutes/BPS/bonus/stats into each DGW fixture row. An in-memory 180-minute, 8-point, 40-BPS, 2-bonus input produced totals **360, 8, 80, 4**. `features.py:34` trains per fixture. Empty explain creates fixture 0, interpreted as a missed match (companion F004). `pipeline.py:108-115` skips any GW already present forever; plan refresh lacks `--points` (`plan.yml:91-94`), leaving midweek history behind (F005).
- Steps: obtain genuine fixture statistics from supported element-summary history; use explain for quantities it actually contains; mark missing fields unknown, never divide aggregates or invent starts/xG. Exclude non-fixture placeholders from training while retaining legitimate zero-minute appearances. Validate completeness/finalization and selectively reload missing/unsettled GWs, including plan refresh. Add reconciliation counts and history-through-GW health. Produce a repair report for existing DGWs; execute repairs only on a backed-up copy first.
- Depends: A01. Persisted history repair is a separate approved data operation.
- Accept: two-fixture totals reconcile exactly with the input; fixture minutes/starts are correct or explicitly missing; blank placeholders leave minutes training unchanged; empty/truncated finished feeds cannot erase valid history; repeat refresh is idempotent; a newly finished midweek GW loads before planning.
- Verify: `uv run pytest -q tests/test_db.py tests/test_pipeline.py tests/test_minutes.py tests/test_quality.py tests/test_workflows.py`.

### [x] A05 | P1 | Make persistence recoverable and imports idempotent

Paths: `scripts/{data_restore,data_publish,plan_publish}.sh`, `src/fpl_agent/db/database.py`, `.github/workflows/{data,plan,review,backfill}.yml`, `tests/test_db.py`, `tests/test_workflows.py`; **new** `tests/test_persistence.py`.

- **V:** restore treats every download failure as first run and does not fail reliably on extraction error. Publish clobbers the only database archive without manifest/hash/count checks. `database.py:277-306` leaves old parquet files for now-empty tables, interpolates filesystem paths into SQL literals, and re-imports append-only tables without stable record identity. Reusing an export folder can resurrect emptied data. Companion F003/F049/F089 concern stronger absent release machinery; do not copy it blindly.
- Steps: distinguish missing release from authorization/network/corrupt archive errors; fail closed for the latter. Export to a fresh staging folder with table counts/checksum/schema metadata and promote only after validation; use safe DuckDB path handling, including apostrophes/Windows paths. Make restore/import into a fresh DB or demonstrably idempotent for ledger tables; avoid merging the same archive repeatedly. Keep a small configurable rotation by age/count and test restore from a known-good copy; do not delete existing release assets as part of this task. Preserve compatibility with the current legacy archive via an explicit path.
- Depends: A01/A04. Can precede dashboard.
- Accept: corrupt/unauthorized downloads fail without replacing data; empty table stays empty after roundtrip; repeated import does not duplicate decisions/projections/reviews/logs; export works under a path containing an apostrophe; historical artifacts remain available for rollback. Plan workflow does not publish an invalid/absent export after failure.
- Verify: `uv run pytest -q tests/test_db.py tests/test_persistence.py tests/test_workflows.py`; shell fixture checks in Linux CI, no real release upload.

### [x] A06 | P1 | Record runs, freshness, delivery and deadline coverage

Paths: `src/fpl_agent/{agent,health,notify,config,cli}.py`, `db/schema.py`, `execution/safety.py`, `solver/timing.py`, `.github/workflows/{plan,token-check}.yml`, `tests/test_agent.py`, `tests/test_notify.py`, `tests/test_timing.py`, `tests/test_workflows.py`; **new** `tests/test_run_history.py`.

- **V:** freshness rail checks GW equality, not age (`safety.py:98-104`); pool drops snapshot timestamp. Early skips/errors return without output. Decision is saved before execution; DB lacks run/outcome linkage (`schema.py:57-73`). `agent.py:280-281` ignores notification failure, suppressing later scheduled retry once a decision exists. Held moves bypass all execution (`agent.py:261`), including an otherwise safe lineup. Blocked outcomes may exit 0. Action window 0.75-26h and transfer hold until 5h rely on cron arriving; `.github/workflows/plan.yml:10` promises cadence, not delivery. **E:** companion F001's actual missed-run percentages are not reproduced.
- Steps: add a minimal versioned run record with ID, UTC start/end, stage durations/status, GW/season/entry, input/projection IDs, outcome, blocked/hold reasons and delivered-state record; persist all terminal paths. Separate planned, applied, verified and delivered states. Carry snapshot age and last successful source refresh/coverage into rails and UI; missing/old required input must block writes with a reason. Use configurable documented freshness thresholds (initial snapshot threshold 6h); timestamps must reflect successful fetches, not match row edits. Retry failed delivery with bounded backoff and mark delivered only after all chunks succeed. Set an owned-squad lineup/captain independently while transfers are held. Alert on actionable blocked/failed runs and lack of a verified run before a configurable deadline offset. A local dashboard is not a scheduler guarantee: document an explicit local scheduler/manual fallback; external heartbeat deployment is deferred.
- Depends: A01-A04; run record is prerequisite for D01/D02. Keep records entry-scoped; current decisions are not keyed by entry.
- Accept: every skip/error/held/blocked/success has a durable terminal status; failed delivery retries next run without duplicate successful delivery; stale/unknown snapshots block; held run can safely set the owned XI without transfers; missed-run simulation raises an alarm. Kill-switch parser accepts explicit false/0/no/off consistently; invalid values cannot enable writes. Preserve existing scheduled dry-publish behavior; do not claim companion F082 is a production defect.
- Verify: `uv run pytest -q tests/test_agent.py tests/test_notify.py tests/test_timing.py tests/test_execution.py tests/test_run_history.py tests/test_workflows.py`.

### [x] A07 | P1 | Remove evaluation leakage and score actual outcomes

Paths: `src/fpl_agent/model/{project,backtest,replay,retro,features,translation,calibration}.py`, `review.py`, `db/schema.py`, `rules/2026_27.yaml`, `tests/test_backtest.py`, `tests/test_replay.py`, `tests/test_retro.py`, `tests/test_review.py`, `tests/test_calibration.py`; season rules files **new only after verification**.

- **V:** `project.py:129-139` prefers closing odds without a time cut; `build_projection` does not pass an as-of timestamp to `current_pool` and loads current translations (:214). Backtest/replay cut at first-kickoff date, not deadline. Schema upserts lose earlier odds/calendar versions. `review.py:50` takes any one fixture explain, :66 uses newest projection regardless of deadline/run, :140 ignores auto-subs, vice fallback and chips. Calibration points are saved after scaling (`agent.py:200-209`), while component expectations remain unscaled. Only 2026 rules exist; `Rules.nearest` silently substitutes for older scoring. Companion F017/F051-F057/G1.2/G4.1/G1.3 align with these limitations.
- Steps: propagate an exact UTC decision cutoff and common pricing-horizon rule; reject post-cut/closing odds, later snapshots and later translation fits. Store append-only odds and fixture observations prospectively; distinguish event-time reconstruction from genuinely known-at data. Unknown historical timestamps/availability/scout/ownership/calendar must be labeled, not fabricated. Store raw projections plus separately labeled calibrated/decision values and model provenance. Select one pre-deadline projection run for review, aggregate all fixture components, use season-specific position. Score actual entry picks and official hits separately from hypothetical plan, reusing rules auto-subs/captain/chip scoring. Separate historical official-rules results from current-rules counterfactuals. Report live/replay policy and coverage differences, including ownership, recent hits, refit cadence, horizon and prior share.
- Depends: A04/A06; correctness gate before model tuning A10.
- Accept: injecting later odds/snapshots/translation/calendar observations cannot alter an earlier prediction; assertions cover backtest, replay and retro. DGW component totals reconcile; a no-show captain uses vice, auto-sub and BB/TC/FH scores match fixtures. Raw calibration input remains raw across repeats. Unsupported exact reconstruction produces an explicit limitation. Recompute baseline comparisons on paired rows; record per-position RMSE/MAE/bias and replay totals with matching configs/seeds, not a promise of higher points.
- Verify: `uv run pytest -q tests/test_backtest.py tests/test_replay.py tests/test_retro.py tests/test_review.py tests/test_calibration.py tests/test_project.py`.

### [x] A08 | P1 | Reduce repeated API/model/solver work with evidence

Paths: `src/fpl_agent/{agent,scout}.py`, `data/{fpl_client,ownership,oddsapi,fotmob,understat}.py`, `model/project.py`, `solver/{mip,chips}.py`, `tests/test_data.py`, `tests/test_ownership.py`, `tests/test_oddsapi_scout.py`, `tests/test_planner.py`, `tests/test_decide.py`.

- **V/R:** `agent.py:105-106` calls bootstrap twice; a counting fake confirmed 2 requests. Ownership cold fill does 6 standings plus up to 200 sequential picks (`ownership.py:30-66`), with no minimum useful sample before weeklong caching. Every run refits (`project.py:206-208`), and chip search solves each available chip/GW (`chips.py:126-143`). Solver accepts an incumbent but discards status/gap (`mip.py:86-88`). Client ownership/closure is generally implicit. **O:** actual runtime hotspots/speedup unknown.
- Steps: use one validated bootstrap object per run; instrument counts/durations first. Add a bounded elapsed/request budget for optional ownership sampling and minimum useful sample, retaining documented fallback. If profiling justifies it, use a small rate-limited fetch pool with deterministic aggregation and serialized DB writes; preserve FotMob/Understat politeness delays. Honor Retry-After with capped backoff; close owned clients, retain injected-client ownership. Reuse fits only under exact history/cutoff/code/config keys; invalidate on ingest. Expose MILP status/gap/time and validate incumbent feasibility; do not label time-limited solves optimal. Optimize chip scoring only if measured, preserving worlds/decisions.
- Depends: A06 instrumentation, A07 for cache keys.
- Accept: exactly 1 bootstrap GET per plan; cold ownership has an injected testable budget and never stores a tiny partial sample as representative; cache invalidation tests pass. Record median stage runtime/request counts on identical data before/after; target >=20% reduction in the measured changed stage with equal seeded projections/legal decisions. If unmet, retain only demonstrated reductions and report it; do not weaken correctness thresholds to meet speed goals.
- Verify: `uv run pytest -q tests/test_data.py tests/test_ownership.py tests/test_oddsapi_scout.py tests/test_planner.py tests/test_decide.py tests/test_agent.py`; benchmark offline recorded/synthetic inputs with elapsed time and request counters.

### [x] A09 | P1 | Bound AI cost and expose scout failures

Paths: `src/fpl_agent/scout.py`, `agent.py`, `db/schema.py`, `discord/src/worker.js`, `discord/test/worker.test.js`, `tests/test_oddsapi_scout.py`.

- **V:** scout caps 40 players/1500 output tokens but selects by price including n/u players it later refuses to adjust (`scout.py:61-66,95`). It ignores stop_reason/usage and maps invalid/truncated output to empty success. No answer cache or call provenance. Discord sends both full Markdown and JSON and allows 4000 output tokens for a <1500-character answer (`worker.js:115-131`). **E:** companion F012 production zero-adjustment rate; model identifiers/provider support have not been checked here.
- Steps: exclude ineligible players, prioritize squad/plausible candidates with relevant news; cap text/input bytes; include decision date/fixture dates; JSON serialize input safely. Cache validated result by model/prompt version/season/GW/player/status/chance/news hash/date context with expiry. Record sanitized counts, usage, stop reason, latency, parse rejects and cache hit; distinguish no eligible news from failed/empty/truncated response. Keep bounded overlay and no-key fallback. Send compact selected plan context to Discord, with clear data age; cap output to the task. Retain model configurability; verify official provider docs only if changing model/structured-output features. Compare cheaper model only after a recorded evaluation, no invented availability/pricing.
- Depends: A06 for call/run provenance; A08 cache conventions.
- Accept: unchanged context within TTL causes zero new scout calls; changed news/model/prompt/date invalidates. Truncation/empty parse with sent players is visible; n/u cannot be revived; malformed/unknown/duplicate IDs are handled deterministically. Mock usage captured; no keys or raw auth responses stored. Discord input bytes reduced >=30% on a saved representative report, with fixtures pinning facts/no-data behavior; output budget reduced from 4000 to <=1000 unless measurement justifies otherwise.
- Verify: `uv run pytest -q tests/test_oddsapi_scout.py tests/test_agent.py`; `npm ci` then `npm test` in `discord/` when Node is available.

### [!] A10 | P2 | Improve FPL intelligence after input/evaluation repairs

Paths: `src/fpl_agent/db/{ingest_fotmob,ingest_vaastav,ingest_understat,quality}.py`, `data/id_map.py`, `model/{players,minutes,features,simulate,project,translation}.py`, `solver/{decide,chips,inputs}.py`, related existing tests.

- **V:** FotMob and archive ingestion link unique full names without club/season/position proof (`ingest_fotmob.py:84-100`, `ingest_vaastav.py:77-91`). `players.py:125-140` reuses attacking-share prior for assists; `project.py:101-113` adds penalty credit atop penalty-inclusive xG; `simulate.py:44` fixes assist probability at 0.75 and `_minutes` rescales outfield starts proportionally. Features use earliest GW kickoff instead of each player's fixture. `translation.store` has no production caller; loading an empty table falls back to fixed priors, not learned translations. **E:** companion identity counts, particular player xP errors and calibrated constants are not current measurements.
- Steps in order: (1) report link/prior/feature coverage; add stable source-ID plus season/club/position-aware matching and explicit ambiguous overrides; add collision checks. Do not auto-merge/split historical identities; produce a backed-up repair proposal. Preserve missing defensive stats as unknown and source precedence for FPL calendar; isolate malformed optional pages. (2) Separate xA prior and align FPL positions; remove penalty double count using supported non-penalty evidence or disable unvalidated additive credit. (3) measure minutes/assist/DefCon calibration by position and history coverage; fix blank/label/fixture-rest problems before parameter changes. Fit translation only on pre-cut data, outside the hot plan path. (4) expose ownership policy xP tradeoff/fallback and last-week chip contamination; recompute reported captain xP after overrides; test chip selection independent of YAML order and quantify expiry opportunity cost.
- Depends: A04/A07/A08. Deliver as separate small experiments; do not bundle a model rewrite.
- Accept: no namesake/cross-position merge in adversarial fixtures; ambiguous links stay unlinked with visible reason. Per-fixture FPL calendar precedence holds. xG/xA prior tests use different known truths; no duplicated penalty credit. Publish paired holdout metrics and prior shares per season; retain behavioral tuning only if intended calibration improves without material overall regression, using a predeclared tolerance and uncertainty. Add targeted tests to `test_players.py`, `test_minutes.py`, `test_fotmob.py`, `test_understat_idmap.py`, `test_backfill.py`, `test_decide.py` and `test_project.py`.
- Verify: `uv run pytest -q tests/test_players.py tests/test_minutes.py tests/test_fotmob.py tests/test_understat_idmap.py tests/test_backfill.py tests/test_decide.py tests/test_project.py tests/test_calibration.py`; real-data backtest/replay commands below only after prerequisites.

### [x] A11 | P2 | Operational and dependency hygiene

Paths: `.github/workflows/{plan,token-check,data,backfill,discord-deploy}.yml`, `src/fpl_agent/execution/auth.py`, `config.py`, `cli.py`, `pyproject.toml`, `README.md`, `docs/architecture.md`, `tests/test_workflows.py`, `tests/helpers.py`, `tests/test_backfill.py`, `src/fpl_agent/data/store.py`.

- **V/potential risk:** token-check and live plan use different concurrency groups; token-check's one-time busy probe cannot atomically exclude a subsequently starting plan. Actual collision rate unknown (companion F050 skeptic also narrows the claim). Long-lived keys are job-level env. Some manual inputs are interpolated directly into shell text. Wheel packages only `src/fpl_agent`, but `rules/engine.py:12` expects repository-root rules. CLI DB handles generally lack explicit close. No installed dependency advisory assessment was run.
- Steps: coordinate token users with a dedicated authentication lease/freshness check; do not place token renewal behind hours-long backfill. Scope credentials to needed steps; pass manual input through env and validate types/allowlists. Preserve rotation-writeback-before-moves invariant; do not adopt companion F087's suggestion to continue after failed persistence. Check built-wheel rules resources from outside repo and package them if missing. Close DBs explicitly. Specify UTF-8 for repository text fixtures and Unicode report/snapshot reads/writes; make POSIX executable-bit checks platform-aware while preserving Linux/index verification. Verify frozen install in clean supported Python, run available advisory tooling and record findings before updates; no blanket upgrades or unverified vulnerability claims. Correct six/eight-week and dry/live-unverified documentation according to evidence, without claiming remote deployment state.
- Depends: A02/A05; defer architecture for token locking until current workflow tests capture requirements.
- Accept: simulated overlapping auth users cannot use one token concurrently; no backfill-induced renewal starvation; invalid manual inputs cannot become shell code; isolated wheel loads Rules; CLI failure paths release DB locks; default Windows encoding handles fixtures and Unicode reports correctly; Linux still verifies script execution mode; clean frozen installation/check output recorded. Advisory findings include exact package/version, source, applicability and remedy.
- Verify: `uv run pytest -q tests/test_execution.py tests/test_cli.py tests/test_workflows.py`; `uv build` and isolated wheel smoke; Linux CI workflow checks. Lockfile changes only for demonstrated packaging/security needs.

### [x] D01 | P1 | Local dashboard: read-only MVP

Paths: **new** `src/fpl_agent/dashboard/{__init__,server,service}.py`, `dashboard/static/{index.html,app.js,styles.css}`, `tests/test_dashboard.py`, `docs/dashboard.md`; existing `cli.py`, `pyproject.toml` (package assets only), `README.md`.

- **V:** no local dashboard; existing reports/DB/CLI supply most required data. CLI status reports configuration presence, not run health (`cli.py:130`). Current decisions contain no entry ID or durable outcome; do not infer successful execution from a plan row.
- Steps: implement the dashboard architecture below; typed explicit JSON contracts, allowlisted queries, pagination and filters. Show last-deadline public squad separately from authenticated-current squad; dry dashboard never signs in. Support historical legacy rows labeled entry/execution unknown. All eight navigation sections exist; unsupported metrics/actions explain their prerequisites.
- Depends: A06 run record; read-only prototype may use existing tables before outcomes exist, showing unknown. A05 provides consistent data access.
- Accept: localhost-only, no new runtime dependency, no build/CDN requirement; starts with missing/empty DB and renders setup guidance. Seeded test records reproduce counts/player IDs/xP/outcomes exactly. Overview, team, recommendations, players, planning, activity and data-health load without network calls; controls initially disabled pending D02. No secret-bearing raw JSON or arbitrary SQL/path endpoint. Keyboard navigation and 375px/1280px layouts verified. Cached page/API reads target p95 <500ms on a documented representative DB; first load/DB snapshot costs reported separately.
- Verify: `uv run pytest -q tests/test_dashboard.py tests/test_cli.py`; proposed `uv run fpl-agent dashboard --help`; HTTP/browser smoke described below.

### [x] D02 | P1 | Safe local controls and setup

Paths: same dashboard files; **new** `dashboard/jobs.py`, `tests/test_dashboard_controls.py`; `config.py`, `cli.py`, `docs/dashboard.md`.

- **O:** local operation currently requires separate CLI invocations and has no progress/lock visibility.
- Steps: add a fixed action registry for public refresh, dry plan and review; execute existing CLI in managed subprocesses with fixed argument arrays (`shell=False`), server-side validation and sanitized environment. Force dry plan settings regardless of inherited AGENT_MODE, strip FPL auth/PAT/webhook credentials, disable notifications; keep only opt-in optional model keys server-side. Persist job ID/status/exit/output and prevent concurrent local writers. Validate form settings entry/season/horizon/samples and caps; apply only to this local session or a dedicated non-secret preferences file, not repository/GitHub config. Add POST-only controls with session token and Host/Origin validation, allowlisted DB/output location, bounded output, timeouts and cancellation/recovery. No live-transfer, token-check, shell, deployment or secret-edit button.
- Depends: D01/A01-A06; controls enabled only after safety tests pass.
- Accept: two simultaneous write attempts return one accepted job and one busy response; server remains responsive during a slow plan. Foreign origin/host, missing token, invalid action/path/entry/ranges and GET mutations rejected. `AGENT_MODE=live` inherited by server still yields zero authenticated requests in dashboard dry jobs. Restart exposes interrupted status; failed/blocked job is visible. Updated data appears only after writer closes and a consistent snapshot is promoted.
- Verify: `uv run pytest -q tests/test_dashboard_controls.py tests/test_dashboard.py tests/test_execution.py tests/test_agent.py`; browser refresh/dry-plan/review smoke with mocked sources, no account writes.

### [!] A12 | P3 | Optional improvements after measured delivery

Paths: `discord/src/worker.js`, `discord/wrangler.toml`, `.github/workflows/plan.yml`, `docs/dashboard.md`; future test module **new** `tests/test_deadline_coverage.py`.

- **O:** a separate reliable clock can improve deadline coverage; comparison/scenario tools may improve dashboard usability. No deployment is part of the first release.
- Steps: evaluate reusing the existing Worker for deadline-offset dispatch plus independent heartbeat; specify dedupe, rate limits, credentials and recurring operational cost before implementation. Consider saved player comparisons/scenario views only after D01/D02 usage identifies a need. Keep price prediction/congestion/simulator refinements experiment-only until A07/A10 evidence supports them.
- Depends: A06/D02 and measured missed-deadline evidence.
- Accept: clock simulation covers moved deadlines, retries, duplicate dispatch, disabled agent and outages; exactly one action per intended offset, out-of-band missed-run alert. Optional UI views use real stored projections and labeled scenarios. Any hosting changes require a separate deployment handoff.
- Verify: proposed `uv run pytest -q tests/test_deadline_coverage.py`; `npm test` in `discord/` for Worker changes.

## Dashboard plan

### Technology and integration

Use Python standard-library `wsgiref.simple_server` bound explicitly to `127.0.0.1`, a small WSGI JSON/static application, and plain HTML/CSS/ES-module JavaScript. Existing stack has no web framework; this keeps installation lightweight and avoids a second frontend build/runtime. Serve only packaged allowlisted assets. No Sites/cloud hosting, Streamlit, React or additional runtime dependency is needed for this local scope. Package dashboard assets so installed CLI works outside the checkout.

`service.py` owns read/query/serialization contracts; `server.py` routes requests; `jobs.py` owns allowlisted background processes; browser renders escaped text using DOM APIs. Never serve the repository root or raw DB. Bind address is fixed loopback in the first version; reject unexpected Host/Origin, disable CORS, add CSP/frame protections, use unpredictable per-session control token, cap request bodies and never expose environment values. No arbitrary command/SQL/file APIs. Standard-library server is a single-user local tool; remote/multiuser hosting is unsupported.

DuckDB concurrency: `FootballDB` currently creates tables on open, so do not instantiate it for a read-only page request. Open an existing DB read-only through a narrow adapter. Serialize local jobs with one OS-backed lock; close page DB connections before a writer starts. Build a small consistent read-model snapshot while writers are idle; serve it from memory/files while a subprocess writes, label refresh-in-progress, then replace after close. Detect an externally held DB lock, report busy and retain the last good view. Do not share one DuckDB connection across processes/threads or copy an actively written DB file. Bound snapshot payloads; query paginated player/history slices when idle. New provenance tables are additive/versioned and legacy rows remain readable.

Proposed routes: `GET /api/overview`, `/api/team`, `/api/recommendations`, `/api/players`, `/api/planning`, `/api/activity`, `/api/health`, `/api/jobs/<id>`; `POST /api/jobs` and `/api/preferences`. GET never refreshes sources or runs a model. Cache invalidation follows completed job/data version; visible refresh is explicit. Each response includes source/captured-at/run ID and missing/stale indicators where applicable.

| Section | Real source and behavior | Missing-data behavior |
|---|---|---|
| Overview | Settings presence/mode, run records, public entry history, bootstrap deadlines; UTC and browser-local countdown | No heartbeat means status unknown; display setup/last observation |
| My Team | Public picks/history + `parse_squad`, canonical IDs, season snapshots; formation, prices, bank/FT, metrics | Label last-deadline squad; current post-transfer squad requires authenticated capture, unavailable in dry UI |
| AI Recommendations | Decisions, matched projection run/detail, safety/outcome; transfers, captain/vice, XI/bench/chips, xP uncertainty | No decision means no recommendation; distinguish planned/held/blocked/applied/verified |
| Player Analysis | Season snapshots, fixture history, projections and coverage; search/filter and 2-4 player comparison | Unknown stats stay null; show prior/linked/missing history rather than invented ratings |
| Gameweek Planning | FPL-linked calendar, projection horizon, stored decision horizon/chip reasons | No fabricated future decisions; final-calendar archive caveat and postponed/unassigned fixtures explicit |
| Activity | Runs, stage/job status, decision/review/lesson history, bounded sanitized logs | Legacy decisions show outcome/entry unknown; source failures need A06 persistence |
| Controls | D02 local allowlist and session preferences | No live execution or secret management; blocked actions state prerequisites |
| Data Health | Successful ingest_log timestamps, snapshot age, quality/coverage, source call status, optional-key presence | Separate last success, last failure, not configured and never checked; API availability only from actual probes |

### Installation, startup and testing

Prerequisites: supported Python (develop/test on 3.13), uv for documented setup, internet for initial public data; optional entry ID for My Team, optional provider keys for scout/odds. Node is only needed for Discord development, not dashboard. Existing GitHub release download helpers require Bash, gh and zstd; Windows dashboard startup must not depend on them. Offer direct CLI public refresh or an explicitly documented compatible local import.

Proposed after implementation:

```text
uv sync --frozen
uv run fpl-agent dashboard --db data_cache/football.duckdb --port 8765
# Open http://127.0.0.1:8765 (localhost-only)
# First refresh through Controls after D02, or existing db-update CLI.
```

Provide a first-run checklist, optional `FPL_ENTRY_ID`, date/season selection and safe shutdown/backup/restore instructions. Preferences have no secret values. Dashboard may start without a DB; it must not silently import/export/migrate production data. Local preferences do not alter remote Actions behavior. Maintenance is frozen dependency sync, tests and documented additive schema compatibility; no always-on service required.

Tests use in-memory/temp DB and mocked HTTP/processes: JSON contracts, escaped news/logs, null/stale/no-entry states, filtering/pagination, exact projection-to-decision linkage, security controls, job lifecycle and DuckDB locks. Browser checks: phone/desktop, keyboard, contrast, empty/error/loading states, formation, comparisons, long names and no external requests. Use available browser tooling; if unavailable, explicitly report manual validation pending instead of adding a test dependency silently.

## Implementation phases and execution order

1. **First phase: correctness patch only.** A01, A04, A02, A03; use separate commits and targeted regressions. Resolve A11's observed Windows test portability failures as a small prerequisite if developing here; do not suppress genuine failures. Finish with all offline tests and existing CI gates. No model retuning, dashboard build or live write trial. Acceptance is fail-closed ingestion, correct DGWs, one binding transfer operation with complete reconciliation, and stable active chips.
2. **Trustworthy state and operational visibility.** A05 then A06; implement A11's credential scoping/token regression tests if needed to make live-state handling safe. Test backups/restarts/offline failures before promoting persistent data changes.
3. **Dashboard delivery.** D01 read-only first, then D02 controls. Deliver each independently; do not block a truthful read-only UI on model tuning. UI labels unresolved historical/evaluation limits.
4. **Measured efficiency and evaluation.** A07 before model/fit caches; A08 then A09. Baseline profiling from A06 can begin earlier. AI call caching can proceed without waiting for historical backtests if its input keys are complete.
5. **Secondary work.** A10 as isolated input/model/policy experiments, then remaining A11 packaging/security work. A12 deferred. Do not port every companion finding or absent fixed-branch feature into this phase.

## Progress, verification and reporting

### Audit evidence recorded by Codex

- Read architecture/configuration/CLI/orchestration, source/ingestion contracts and identity paths, model/projection/evaluation/planner/chip logic, execution/auth/rails, persistence/automation, Discord, and test coverage/layout. Skipped dependencies, generated caches, fixture payload dumps, secret-bearing configuration and bulk local data. Companion audit reconciled as above; production data/remote commits not inspected.
- `.venv/Scripts/python.exe --version`: **Python 3.13.12**.
- `.venv/Scripts/ruff.exe check . --no-cache`: **All checks passed**.
- `.venv/Scripts/ruff.exe format --check . --no-cache`: **116 files already formatted**.
- `.venv/Scripts/python.exe -B -m fpl_agent.cli --help`: **exit 0**, existing commands listed; no dashboard.
- Offline in-memory probes: critical player ingest **exit 0 with critical_failed=True**; DGW input/output totals and duplicate bootstrap counts recorded in A01/A04/A08. These are probes, not added regression tests.
- Full offline suite: `.venv/Scripts/python.exe -m pytest -q -p no:cacheprovider` with `PYTHONDONTWRITEBYTECODE=1`: **exit 1; 7 failed, 474 passed, 1 skipped, 13 deselected in 327.16s**. Six failures were in `tests/test_backfill.py` (CP1252 fixture decoding errors plus downstream missing-data assertions); one was `tests/test_workflows.py::test_helper_scripts_are_executable_and_strict` (Windows filesystem mode). Git index modes were not verified because Git is unavailable. These failures are part of the handoff, not a passing baseline. No coverage gate, clean frozen install, Node tests, advisory scan or real-data performance claim has been verified in this audit.
- Follow-up `.venv/Scripts/python.exe -X utf8 -m pytest -q -p no:cacheprovider tests/test_backfill.py` with bytecode disabled: **exit 0; 14 passed in 16.71s**, confirming the six backfill failures are encoding-dependent. The full suite was not repeated under UTF-8; executable-bit failure remains unresolved.
- Plan validation: 14 unique task IDs; every task has affected paths, dependencies, acceptance criteria and verification commands; first-phase order and all eight dashboard sections checked. Only `TASK.md` was intentionally authored; Git diff/status could not be obtained in this shell.

### Claude completion record (update per task)

Record task ID, changed paths, concrete behavior, actual command/exit/result, before/after measurements where applicable, limitations/blockers, and any data repair/rollout still pending. Separate mock verification from production evidence. Add new-test filenames to this plan when created. A test plan is not a test result.

Session 2026-10-20 (Claude). Branch `task/phase1-dashboard`, uncommitted (no commit was requested). Environment: `uv` not on PATH, so every command ran through `.venv/Scripts/python.exe` (Python 3.13.12); pytest needed `--basetemp` inside the sandbox temp folder. No AGENTS.md exists in the repo or user folder; this file and CLAUDE.md governed. All execution tests use mock transports; no authenticated FPL request was made. The only network use was one public, unauthenticated `db-update` through the dashboard (see D01/D02).

- **A01 [x]** `contracts.py`, `pipeline.py`, `tests/test_contracts.py`, `tests/test_pipeline.py`. Bootstrap and fixtures are both fetched and validated before any write, including `repair`. Contracts check every row (`_each`), not just the first, plus element→team and fixture→team references. Fixtures are critical. Bootstrap and fixtures load in one transaction, and a failure there returns 2 with no export. A finished GW's live feed must not be empty (`fpl_live(finished=True)`). Optional sources still degrade. Tests: fixture failure (3 shapes), injected `FootballDB.team_id` failure (before: exit 0), malformed last row, untouched DB including no repair marker, empty finished feed, last-row/unknown-team contract cases. Workflow: db-update exit 2 fails the step, so the plan step does not run (`plan.yml` has no `if: always()` on Plan).
- **A04 [x]** `db/ingest_fpl.py` (`split_stats`, `load_live`), `model/features.py`, `pipeline.py` (`_points_due`, `load_points`, `reload_gws`), `db/quality.py` (`dgw_history_legacy` warn), `cli.py` (`--reload-gws`), `.github/workflows/plan.yml` (refresh now passes `--points`). DGW totals are split from explain values and are exact only when they reconcile. Otherwise the value is null (unknown); totals are never copied or divided. Zero-minute fixtures of the player's team get real rows. Only blank GWs get fixture 0, which training excludes (as it does unknown-minutes rows). A GW reloads until it is stored with `data_checked`. Legacy GWs are left alone and flagged; repair is opt-in with `--reload-gws` on a backed-up DB. Truncated feeds are rejected. Tests: 180/9/40/2 input reconciles exactly to 180/9/40/2; split never invents; blank placeholder leaves `player_gw_frame` identical; truncated feed preserves rows; repeat refresh idempotent; newly finished GW loads; finalised GW not refetched; legacy warning. **Pending:** nothing has been reloaded in any persisted production DB. Run `db-update --points --reload-gws <legacy DGWs>` on a backup first.
- **A02 [x]** `execution/execute.py`, `execution/fpl_writer.py` (`preflight`, `readback_diff`, `active_chips`; `preview_matches` removed), `agent.py`, `tests/test_execution.py`, `tests/test_agent.py`. Live path: read my-team, then preflight against it (owned out / not-owned in, FPL selling prices, bank ≥ 0 and equal to the plan, hit cost from `limit/made`, no other active chip), then exactly one `confirmed: true` transfers POST. After any error or lost reply the team is re-read before anything else and nothing is resubmitted. The lineup runs only after transfers are confirmed in place. Final read-back compares squad, XI set, bench order, captain, vice, chip and bank, and lists fields FPL did not expose in `Outcome.unverified`. `live-done` now requires `verified is True`, so held, blocked and unverified runs no longer qualify (before: a held run could be saved as live-done). Tests: first POST applies and a second would 400 (one POST, lineup done); lost reply; 4xx after applying; not applied; wrong outgoing player; 14-player squad; wrong bank/cost blocked pre-send; bank differs after; lineup ignored; repeated run; unverified fields; agent live-done matrix. **Limitation:** the my-team field names and transfer payload are not pinned to a captured response from the owner's account (none was supplied). They follow the existing code. Live validation is a separate authorised rollout.
- **A03 [x]** `data/entry.py` (`active_chip`, FH purchases ignored), `execution/live_state.py`, `solver/decide.py`, `execution/safety.py` (`second_chip` rail; active chip passes availability), `execution/execute.py`, `agent.py` (after an FH week, picks from the GW before it). Tests: BB/TC/FH active keep the chip with no chip search; FH squad locked and lineup-only; second chip blocked; BB replan re-sends `chip: bboost` with no transfer POST and verifies; FH-week purchase prices ignored; agent reads GW-2 picks after an FH. **Unknown:** whether FPL needs the chip repeated in a lineup POST for an already active BB/TC is not confirmed by captured evidence. Read-back checks that the chip is still active.
- **A08 [!] (partial)** `data/fpl_client.py` (`bootstrap(raw)`), `agent.py`: exactly 1 bootstrap GET per plan (fake counter: was 2). Tests: `test_a_plan_fetches_the_bootstrap_once`, `test_the_client_parses_an_already_fetched_bootstrap_without_a_request`. Not done: ownership budget, Retry-After, fit reuse, MILP status/gap, profiling.
- **A11 [!] (prerequisite part only)** UTF-8 for fixture reads/writes in tests; `test_helper_scripts_are_executable_and_strict` checks the Git index mode (100755) on Windows and the stat bit on POSIX. Full suite now passes without `-X utf8` (before: 7 failures). The rest of A11 was not started.
- **D01 [!]** **new** `src/fpl_agent/dashboard/{__init__,service,server}.py`, `src/fpl_agent/dashboard/static/{index.html,app.js,styles.css}` (inside the package so an installed CLI finds them), `cli.py` (`dashboard` command), `docs/dashboard.md`, `README.md`, **new** `tests/test_dashboard.py`. Uses stdlib `wsgiref` with a threading mixin on 127.0.0.1 only. Host allowlist (421 otherwise); CSP, frame-deny, nosniff, no-store. No new dependency, build step or CDN. The DB is opened read-only for one snapshot and then closed. The view is cached by file version and the last view is kept while a job writes or another process holds the DB (Windows "used by another process" and POSIX lock both detected). All eight sections exist; missing, empty and error states are handled. Legacy `live` rows are labelled outcome unknown, and entry is shown as unknown. Tests (11): missing DB is not created; empty DB; exact IDs/xP/linked projection run (the later run is not used); sections; players filter/sort/page/compare cap; host/route allowlist; headers and token; static assets contain no URLs or `innerHTML`; no secret values in any response; cached p95 < 0.5 s; DB writable by another process between reads; locked DB keeps the last view. Real data: dashboard started with no DB and showed setup state. After the refresh job: 668 players, GW8 next, points through GW6. Snapshot build 63 ms, cached API reads ≈ 2 ms (curl). `node --check` on app.js caught a real syntax error, now fixed. Every section was rendered by the real app.js in a Node DOM shim against the live server (real DB) and against the seeded test DB (team/recommendations/planning populated): no runtime errors. **Pending:** the 375 px/1280 px layout, keyboard and contrast check in a real browser was not done (no browser tooling in this session). Recommendations/team on real data need a dry plan, which needs the owner's FPL team id (not set here).
- **D02 [!]** **new** `src/fpl_agent/dashboard/jobs.py`, **new** `tests/test_dashboard_controls.py`. Fixed actions only: refresh (`db-update --points`), dry plan (`plan --force`, no `--notify`), review. Each runs as `python -m fpl_agent.cli` with an argv list and `shell=False`. The child env forces `AGENT_MODE=dry` and strips FPL auth, GitHub, webhook and Discord credentials. Model keys pass only with `--allow-model-keys`. One writer through an exclusive `writer.lock` holding the owner PID (stale only if that PID is dead; liveness is checked without `os.kill` on Windows). Jobs persisted to `jobs.json` (atomic, Windows-retry); interrupted on restart; 64 KB output cap; 30/10 min timeouts; cancel. POSTs need same Origin plus a per-start token; 4 KB body cap; validated preferences (no secrets) in `preferences.json`. Tests (11, run 3× stable): fixed argv and rejects; env stripping; real CLI under inherited `AGENT_MODE=live` reports DRY; two writers give 202 + 409 while the server answers in < 2 s; second dashboard refused; foreign origin, no origin, missing or wrong token, bad host, bad action, big body and bad prefs rejected; GET never mutates; failed job (exit 3) visible; cancel; restart → interrupted and stale lock released. Real: refresh job via HTTP with the token succeeded (exit 0, 9 s, quality: no issues), a concurrent review got 409, and a POST without a token got 403. **Pending:** real-browser click-through, and a dry-plan smoke on real data (needs the owner's team id). Depends on A05/A06, which are not done; the controls do not rely on them.
- **Session 2 (2026-10-20 evening, Claude): A05-A11.** Offline only. No FPL sign-in, no release upload, no push or deploy. All commands ran via `.venv/Scripts/python.exe` with `--basetemp` under the sandbox temp folder. Another agent (Codex) was restyling the dashboard ("Matchroom") in this same tree at the same time; Claude did not touch the dashboard files after 19:42.
- **A05 [x]** `db/database.py` (`export_parquet` stages into a fresh folder with a `manifest.json` of counts, columns and sha256, verifies, then swaps; keeps `<dir>.prev-*` (2); `verify_export`; `import_parquet` refuses a damaged manifested export, accepts legacy unless `require_manifest`, upserts PK tables, EXCEPT-dedupes ledgers, skips an already-imported manifest; paths quoted safely), `cli.py` (`verify-export`), `scripts/data_restore.sh` (only "release not found" means start empty; auth, download, unpack and manifest failures exit non-zero; refuses a non-empty target; `DATA_ASSET` rolls back to a dated copy), `scripts/data_publish.sh` (verifies first; uploads `football-db.tar.zst` plus a dated copy; never deletes, only lists copies beyond `KEEP_ARCHIVES`), `plan.yml` (publish only if `steps.refresh` succeeded), `review.yml` (pipefail). **new** `tests/test_persistence.py`: 7 Python tests pass. 4 shell tests are skipped here (no `zstd` on this machine) and are meant for Linux CI.
- **A06 [x]** **new** `src/fpl_agent/runs.py`, `runs` table (additive), `agent.py` (`run_weekly` wraps `_run`; every exit recorded with status ok/skipped/held/blocked/failed/error, state none/planned/applied/verified, stage seconds, snapshot/projection/decision links, reasons, delivery; a crash is recorded too; a live global block exits 3), `notify.py` (3 tries per chunk, Retry-After ≤10 s; True only when every chunk lands), delivery (plan re-sent until delivered once per GW and entry; each distinct live failure or block alerted once), `safety.py` (`stale_snapshot` rail, live only, `AGENT_MAX_SNAPSHOT_HOURS` default 6, unknown age blocks), `config.py` (`kill_switch_enabled`: only unset/true/1/yes/on enable), `decide.lineup_only` (a held run sets the owned squad's XI and captain), `write_outputs` (early stops write `run-<status>.md`, never `gw*.md`), `cli.py coverage-check` plus a `coverage` job in `token-check.yml`. **new** `tests/test_run_history.py` (21 pass). `test_agent.py`'s held-run assertion was updated to the new contract.
- **A07 [x]** `project.build_projection(cutoff=, known_at_odds=)`: history by kickoff, pool and translations as-of the cutoff, never closing odds, and only odds logged at or before the cutoff. A test injected after-cutoff odds and caught a real leak through the reconstruction fallback; now a logged match uses its log alone. **new** append-only `match_odds_log` (`db.log_odds` skips unchanged prices). `translation.store` now stamps UTC. `project.approx_deadline` (first kickoff - 90 min, labelled approximate) is used by `backtest.py` and `replay.py`; `retro` passes the exact deadline (`cutoff_fn`). `agent.py` saves raw projections before calibration. `review.py`: one projection run from before the cutoff, all DGW explains summed, season positions, plan scored with auto-sub, vice and BB/TC (labelled hypothetical; actual-entry scoring stays in `lessons`). **new** `tests/test_point_in_time.py` (8 pass).
- **A08 [!]** Done: 1 bootstrap GET per run (was 2); ownership sample has a 90 s budget and a minimum of 60 managers (smaller samples are not stored); `FPLClient` honours Retry-After (capped 30 s) and closes only a client it created; `mip.SolveInfo` (status, optimal, gap, seconds) with an incumbent feasibility check; `Plan.solve`; a decide warning when not optimal. Tests in `test_ownership.py`, `test_planner.py`, `test_data.py`, `test_agent.py`. **Not done:** fit reuse cache and chip-search optimisation (no profile on real-size data justifies them); before/after stage medians on real data.
- **A09 [x]** `scout.py`: n/u never sent; squad first; news ≤300 chars; input ≤12 kB; decision date and deadline line; cache `scout_cache` (key: model, prompt v2, season, GW, date, player, status, chance, news; 12 h); `scout_calls` log (counts, usage, stop reason, latency, outcome; no key or raw reply); outcomes no_news/cached/ok/empty/truncated/parse_error/failed shown in the report; duplicates and unknown ids rejected deterministically. `agent.py` passes db, ctx and squad. Discord `worker.js`: `planContext` (report plus a compact facts JSON plus data age) cuts input bytes 6,692 → 3,699 (−44.7%) on `discord/test/fixtures/plan.{md,json}`; `max_tokens` 4000 → 1000. Tests: `tests/test_oddsapi_scout.py` 17 pass; `cd discord && npm ci && npm test` 14/14 pass.
- **A10 [!]** Done: `FootballDB.name_link` (FotMob and vaastav link by name only when the name has 2+ words, there is a unique candidate, positions agree and the source id is not already taken; otherwise unlinked with the reason in `ingest_state` source 'link'); `quality.player_link_collision` warning; separate measured xA prior (`position_priors()["xa_share"]`); `PENALTY_CREDIT = 0.0` (the old credit double-counted penalty-inclusive xG); per-team own kickoff for minutes; captain xP recomputed after a template override; `fit-translations` CLI (outside the plan path); a test that the chip choice ignores YAML order. One existing fotmob test fixture was wrong (it registered a goalkeeper as MID); corrected. **Not done (needs real data):** holdout calibration of minutes, assists and DefCon by position; npxG-based penalty model; ownership-policy xP tradeoff report; chip expiry cost; defensive-stat unknown handling.
- **A11 [!]** Done: UTF-8 everywhere in `src` (rules, store, id_map, cli); the CLI closes every DB it opened (`_OPENED`, also on failure; test added); rules packaged into the wheel (`force-include` → `fpl_agent/rules/data`, used first); wheel built with the uv base Python's pip, and `Rules.load(2026)` works from the unpacked wheel outside the repo; manual workflow inputs moved to `env:` with validation (plan, backfill, data, backtest), plus a policy test; README and architecture now say an 8-week horizon. Advisory: `npm audit` in `discord/` → 0 vulnerabilities; root `npm audit` → 7 high (`braces` via `shadcn@^4.22.1`, a root devDependency the user/Codex added; dev tooling only; the only fix is `--force` to shadcn@1.0.0, breaking, not applied). Python advisory tool not available (no pip-audit or uv), not run. **Not done:** token lease across token-check/plan; per-step credential scoping (the `status` step reads the secrets' presence); `uv sync --frozen` clean-install check.
- **Other:** added `permissions: contents: read` to the user's new `.github/workflows/playwright.yml` (repo policy test). Not committed (no commit was requested).
- **Handoff for Codex (next steps, in order):** (1) Full gate already run at the end of Session 2: `python -m pytest -q -p no:cacheprovider --cov --cov-fail-under=85` → exit 0, **627 passed, 5 skipped, 13 deselected in 644 s; coverage 92.51%**; rerun after any further change. (2) Dashboard: surface the new `runs` table (Overview/Activity: status, state, delivered, reasons) and `scout_calls`; this replaces the "run history not recorded" note in `service.py`. (3) Finish D01/D02: browser checks at 375/1280 px and keyboard; a real dry plan needs the owner's FPL team id. (4) Remaining A08/A10/A11 items above. (5) A12 (deadline worker or heartbeat) is a design and deployment task needing owner approval. (6) Review: `safety-rails-reviewer` and `leakage-reviewer` agent definitions exist in `.claude/agents` for A02/A03/A06 and A07.

Final gates (2026-10-20, Windows, `.venv`): `ruff check .` → All checks passed. `ruff format --check .` → 125 files already formatted. `python -m fpl_agent.cli --help` → exit 0, includes `dashboard`. `python -c "from fpl_agent.rules import Rules; Rules.load(2026)"` → ok. Full suite: `python -m pytest -q -p no:cacheprovider --cov --cov-report=term --cov-fail-under=85` (default Windows encoding, no `-X utf8`) → exit 0, **547 passed, 1 skipped, 13 deselected in 498.5 s; coverage 92.71%** (before this session: 7 failed, 474 passed). One earlier full run failed 1 test, `test_bad_values_block_export_and_alert`, because its CSV was written with the default encoding; that is fixed and the rerun above is clean. Not run: `uv sync --frozen` (uv unavailable), `uv build` / wheel smoke, Node Discord tests (no Discord changes).

Final phase gates (from repo root unless noted):

```text
uv sync --frozen
uv run ruff check .
uv run ruff format --check .
uv run pytest -q --cov --cov-report=term-missing --cov-fail-under=85
uv run fpl-agent --help
uv run python -c "from fpl_agent.rules import Rules; Rules.load(2026)"
# Discord changes only: cd discord, then npm ci and npm test
```

Real-data evaluation, only on a backed-up local DB containing verified historical inputs (never part of offline CI): `uv run fpl-agent backtest --season 2025 --from-gw 10 --to-gw 30` and `uv run fpl-agent replay --season 2025`. Capture DB path/data identity, missing coverage, scoring regime, config, seed, runtime and raw metrics; use matching rows/seeds for comparisons. Current README accuracy numbers and the companion's production timing/statistics are not this audit's baselines.

### Review verdict

**Approved for the tested corrective code and local dry dashboard.** Independently reran full and targeted gates; fixed additional unknown-stat, auth coordination and real-data lineup eligibility defects. No live rollout, secret update, identity repair or remote deployment performed.

**A08 complete under its measurement condition:** one bootstrap request, bounded sampling, Retry-After, client ownership and solver diagnostics verified. Three paired real-input fits showed no 20% stage improvement (1.0103s vs 1.0286s); retain demonstrated request savings only. No unmeasured model-fit cache or chip-search rewrite added.

**A10 remains partial; the ingestion gap is now addressed:** source/null handling, independent
assist exposure, prior reporting, captain xP cost and chip timing costs are implemented/tested.
Recovered 2024/2025 archives support a measured season-forward minutes holdout, including all
positions (artifacts/minutes-holdout-2025.json). Existing FotMob ingestion now populated the
local production DB with 6,862 appearances / 101,072 player-stat rows, including European cups.
Player-specific projection coverage improved from 0/668 to 304/668; 364 remain prior-based.
Still unvalidated: positional assists/DefCon holdouts, paired overall FPL policy/backtest
metrics, npxG penalty redesign and cross-season club-aware identity repair. Source access now
works; this is unfinished evaluation work, not a claim that the necessary APIs cannot work.
No unsupported accuracy/competitive-edge claim or model-parameter retuning.

**A11 complete for local/code checks:** Browser CI now installs Python and invokes test:dashboard; unused installed CLI packages were pruned (304 packages). Browser suite after pruning: 6 passed in 6.6s. Final focused integration suite (runs/agent/review/dashboard/controls): 78 passed; workflow/data/budget follow-up: 61 passed, 1 skipped.  shared workflow auth lease plus queued-token metadata guard, scoped credentials, injection guards, Unicode handling, frozen install, wheel smoke, dependency audits and documentation verified. Workflow changes are local; five platform/tool-dependent tests remain skipped (including shell persistence requiring zstd). Live OAuth/remote overlap has not been exercised.

**A12 deliberately deferred:** see docs/deadline-worker-evaluation.md. Existing independent Actions coverage job remains; there is no measured missed-run evidence in this checkout to justify a hosted dispatcher/heartbeat. Provider selection, clock simulations and deployment need the separate handoff required by this optional task.
