# Quality-first product roadmap

**Current order of work.** Database trust comes before model improvement. Model evidence comes before the personal-team trial. Only after a reviewed local trial may the company begin external research, hosted website work or monetization. Quality and independent review apply at every gate. No schedule pressure overrides a gate.

## Gate 0 ? Establish the data foundation

**Owner now:** Zaza for pinned archive verification and database-source diagnostics; Nabil provides data/model interpretation; Wegz reviews material claims. God coordinates and accepts the gate.

1. Verify the downloaded production archive against its pinned release identity, byte count and SHA-256 before extraction. Preserve the original and inspect only a scratch copy.
2. Reconcile the archive's actual schema and tables with the declared schema. Explain every missing or extra table; do not treat a materialized scratch schema as proof of original completeness.
3. Measure source freshness, outcomes, coverage and known gaps by table, season, gameweek and decision cutoff. Verify player/team/fixture identities, duplicate/conflicting records, link evidence, and exclusions.
4. Establish which records really existed by each historical decision time. Keep eligible, reconstructed and unavailable history distinct. A new fetch cannot make old data point-in-time eligible.
5. Inventory all candidate signals and their provenance, overlap, coverage and quality. A database field is not automatically a useful model input.

**Exit evidence:** reproducible read-only report on verified input identity; declared-versus-actual schema; source health and field coverage; temporal eligibility/exclusions; identity/conflict findings; no unresolved Required/Critical data defects for the next model experiment. Label unavailable production evidence explicitly. No production migration or replacement follows from this gate.

## Gate 1 ? Build a distinctive, testable model

**Owner:** Nabil. **Independent reviewer:** Wegz. Pablo owns application interfaces; no model edits to shared dashboard files without an explicit handoff.

1. Start from Gate 0 and the existing data-to-decision matrix. For every data family, show used, unused or unavailable, with reason, cutoff, coverage and uncertainty. Do not force irrelevant or duplicated data into the model.
2. Define the FPL objective as decision utility under official scoring, while evaluating player-level predictions and downstream decisions separately. A high points total on one personal team or one season is not proof of a better model.
3. Propose distinctive signal hypotheses that humans or simple baselines may miss. For each, specify the causal or predictive reason, required fields, cutoff safety, expected incremental value, failure modes and a predeclared test. Novelty is a hypothesis until it beats strong baselines on eligible unseen periods.
4. Change one bounded component at a time. Preserve the current model as an explicit fallback; experiments are off by default and saved under distinct model identifiers.
5. Evaluate with chronological, point-in-time, paired player/gameweek comparisons against the current model and relevant simple/market baselines. Report calibration, proper scoring error, cohort coverage, uncertainty and decision outcomes. Show regressions and unchanged cohorts. Respect sample minimums and exclusions. Never train on target/future outcomes or use realized target exposure as deployable forecast evidence.

**Exit evidence:** independent approval of exact source fingerprint; cutoff and leakage checks; reproducible baseline; preregistered paired metrics on eligible samples; acceptable calibration and no disallowed cohort regressions; useful uncertainty; documented limits; operational fallback. If eligible evidence or sample size is insufficient, the model remains experimental. ?Perfect? cannot be established; promotion requires measured evidence and explicit limits.

## Gate 2 ? Validate weekly learning and safe operation

Before a personal-team trial, verify a weekly review loop on recorded or synthetic examples. Freeze pre-deadline forecasts and recommendations; after the gameweek, compare them with outcomes and counterfactual choices. Separate prediction errors, decision errors, bad luck and missing data. A lesson is a versioned proposal, never an automatic live weight/policy change. Test proposed lessons on future eligible periods, retain rollback, and require independent review before promotion. Show that the loop can repeat without hidden credentials, stale inputs or unreviewed self-modification.

**Exit evidence:** tested provenance and cutoff controls; complete prediction-to-outcome trace; useful error taxonomy; repeatable weekly report; no autonomous production/team changes; reviewed lesson proposal, evaluation and rollback.

## Gate 3 ? Local personal-team trial

Only after Gates 0?2 pass, run dry recommendations on the owner's personal team using public/read-only data where possible. Freeze each decision before the deadline; record source freshness, model version, XI/captain/transfers, reasons and uncertainty; review actual points after the gameweek. The owner chooses whether to make any FPL move. Compare over the remaining season with the frozen baseline and transparent alternatives. Track points/rank as noisy outcomes, not guaranteed targets; no model can guarantee the highest rank.

**Exit evidence:** agreed trial scope; complete weekly audit trail; no safety/privacy breach; sustained improvement evidence across the prespecified sample or a clear decision to continue research. No account automation or live writes.

## Gate 4 ? Company readiness research and security

After the local trial is reviewed, decide whether evidence supports customer interviews and product development. Before contact, close consent, retention/deletion, market, data-rights and channel requirements. Validate customer need without implying model accuracy. Then define hosted multi-user boundaries, authentication, privacy, abuse/rate controls, monitoring, support and recovery. Wegz independently reviews security and privacy design.

## Gate 5 ? Website and monetization

Only after Gate 4 passes: build the hosted product in bounded stages; test account isolation and entitlements; select a lawful data path and payment provider; verify checkout, renewal, cancellation, refund and failed-payment handling in sandbox; review applicable market requirements. Launch or charge only after explicit business approval, independent security/release review, backups, monitoring and rollback are ready.

## Current dispatch

- **FAA-7 completed; Gate 0 remains open.** Verified rolling asset `685637934` (195,782,284 B, SHA-256 `3dc27152?c69f`) is from production master `51f0def`; the older pin was superseded and never verified. Production exporter omits empty tables, writes no manifest and replaces one rolling asset. The current local branch already includes manifest export and verification in `database.py`; that code is absent from production master. Pablo?s new slice adds completeness enforcement and safer publish/restore behavior in four files. Eleven absent tables were added only on newer unmerged/uncommitted local code; two older declared tables were empty. Production has no `fpl_deadlines`, `source_attempts`, or `match_odds_log`; 2026 GW1?8 cannot become eligible retroactively. The FPL token is not a prerequisite for public deadline/source-attempt recording, per read-only workflow/code review. Production link coverage is 83.36% of minutes with name-only matching; reviewed development links are not deployed. Evidence: `artifacts/FAA-7-production/r20261028/README.md`, `artifacts/database-gate0/export-gap-matrix.md`.
- **Independent analysis review passed with corrections/conditions.** See `artifacts/database-gate0/review.md`. Gate 0 requires a scoped release; exact diff review and Linux checks; explicit R1/R2 journal behavior; 23-to-34-table migration rehearsal and rollback on scratch copies; prospective records verified before a real deadline; reporting of silent blocked-repair/no-load paths; digest-pinned dated archive; identity updates only through a separate approved action. This is a release candidate plan, not a production authorization.
- **Next:** Gap matrix corrections are complete. Wegz approved Pablo?s R1 local four-file slice with release corrections. Pablo delivered a separate 51f0def-based patch (05d3acd9?af2cff) with an explicit scope map. God verified patch and scope-map hashes; Wegz is reviewing this isolated artifact independently. Windows runs skipped shell tests; Linux CI with zstd, executable mode 100755, archive/rollback rehearsal and prospective writers remain mandatory. No commit, push, production release or Gate 0 pass. Nabil owns the subsequent prospective provenance writers, with a separate diff/review. Zaza prepares isolated archive/migration/rollback evidence. Keep unrelated dashboard, M02 and other dirty work out. No production publish, migration, token/account action or identity-link apply is authorized.
- **Model:** M02 promotion rejected and switch remains off. GW1?8 cannot validate model changes. Start collecting trustworthy prospective data after reviewed remediation; then Nabil refreshes the signal inventory and proposes one testable feature at a time. No new model changes before the data gate passes.
- **Deferred:** API implementation, website build, customer fieldwork, participant sessions and payments. Existing approved drafts stay parked until after model and personal-team gates.
- **Parallel incident:** npm home-folder triage is complete. Recovery choices remain unapproved; no external folder changes.
