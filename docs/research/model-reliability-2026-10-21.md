# Model reliability audit and research plan

Research date: 21 October 2026, Asia/Dubai. Status: **changes needed before commercial release**.

The current system contains reusable forecasting, rules, simulation and optimization code. It does
not yet have evidence supporting a dependable paid prediction product. More model complexity is
not the first priority: repair evaluation, simulation and decision-policy inconsistencies first.
An unusual captain or a losing gameweek is not, by itself, proof of a bad model. Conversely, passing
software tests does not establish predictive skill.

This is a research/audit deliverable. Production model behavior has not been changed in this pass.
The implementation handoff is in [TASK.md](../../TASK.md). Commercial and design decisions are in
[the product strategy](subscription-product-strategy-2026-10-21.md).

## Independently verified findings

| Priority | Finding and evidence | Consequence / required response |
|---|---|---|
| P0 | `model/simulate.py:83`: independent outfield starting draws, with only their expected total capped at ten. | A synthetic 100,000-world probe produced more than ten outfield starters in **40.944%** of worlds, maximum 19. This is a structural counterexample, not the measured incidence for the user's team. Replace with a coherent joint lineup/minutes model; validate its marginals after conditioning. |
| P0 | `model/simulate.py:92` normalizes goalkeeper probabilities and always selects one; `model/project.py:306` retains near-zero-probability goalkeepers. | Two goalkeepers with input start probabilities 0.0001 each become approximately 50/50 starters. An incomplete/unavailable squad requires an explicit unknown replacement state, not confidence invented by normalization. |
| P0 | `model/backtest.py:85` drops missing rows separately for each predictor; `beats_baselines` compares those aggregates. | A reproducible case reports a simulator win (RMSE 1.414 vs 1.8) although on common rows it loses (2.0 vs 1.8). Pair by player/fixture or GW and cutoff before calculating comparisons. Existing claims require fresh evaluation; this probe does not establish that every earlier result was wrong. |
| P0 | `lessons.py:107` treats any nonnegative manual-transfer return as `act_now`; `Memory.planner:400` and `min_win:408` reduce transfer restraint. | A **zero-point gain** activates the rule in the probe. Realized one-week outcomes, without counterfactual opportunity cost or statistical evidence, should not automatically tune policy. Separate retrospective description from validated learning. |
| P0 | `review.py:91` can fall back to a forecast after the deadline. `calibration.py:29` consumes prediction reviews without checking that provenance. `review.py:128` fills unmatched outcomes with zero. | Reject ineligible forecasts from learning/accuracy claims; distinguish missing outcomes from verified nonappearances. A labelled fallback in a report is insufficient if downstream training ignores the label. These paths are verified; their effect on current stored reviews has not been quantified. |
| P0 | `rules/engine.py:53,105,133` uses minutes alone for scoring/participation. | A zero-minute yellow card returns zero in the probe. Official rules include card receipt in participation for autosubs. Add explicit participation semantics and rule fixtures; do not rely exclusively on `minutes > 0`. |
| P1 | `model/minutes.py:139` separately scales start and conditional-cameo probabilities by availability. | If availability means a shared fitness gate, the mixture is inconsistent: hypothetical availability .75, conditional start .8, conditional cameo .3 gives .69 play probability instead of .645. Establish probability definitions and calibrate the complete mixture, not just the classifier. |
| P1 | `model/features.py:29` joins the latest same-GW snapshot; `backtest.py:52` approximates deadlines. | Audit all feature observation times, especially mutable prices, positions and revised data. Event date alone does not prove availability before a decision. The historical minutes study explicitly used reconstructed kickoff snapshots. |
| P1 | `solver/decide.py:280` hard-keeps highly owned players; `rank_target.py:20` defaults everyone to a top-10k target. Captain policy changes after cover refinement at `decide.py:346`. | User objectives and ownership policy are mixed into default decisions. Compare complete final policies with the same scorer. Make rank chasing explicit and opt-in; preserve a points-maximizing reference policy. |
| P1 | `solver/planner.py:41` uses fixed bench weights; `solver/cover.py:14` only refines doubtful-player cases; `decide.py:126` has a separate held-lineup path. | Transfers, chips, lineup, captain and reports do not all optimize/evaluate the same utility. Rerank candidate plans with legal autosubs and final captain fallback, including the hold option. |
| P1 | `solver/value.py:14` permits prior-only players and ranks against a median price peer; `solver/inputs.py` has no corresponding evidence-quality purchase gate. | The watchlist is a screening heuristic, not a validated gem detector. A prior-only forecast can still influence transfers. Add evidence-aware uncertainty and evaluate actual upgrades against the user's squad. |
| P1 | `news.py:20` covers City plus BBC; `features.py:91` derives rest from FPL history. | Imported European fixtures do not establish workload-aware forecasting or all-club news verification. Build these explicitly and measure incremental value. |

Source paths above are relative to `src/fpl_agent/`. Reproduce the offline probes with:

```powershell
.venv/Scripts/python.exe artifacts/product-model-audit-20261021.py
```

Results: [probe JSON](../../artifacts/product-model-audit-20261021.json).
No external services, production database writes or FPL actions are involved.
The official [game rules](https://fantasy.premierleague.com/en/help/rules) are the authority for
scoring and participation, including substitutions and cards; archive the applicable season's
rules rather than silently borrowing another season's behavior.

## What the existing accuracy evidence establishes

A read-only production query found **364 of 668 players (54.5%) using position priors** in the
latest projection `aa41fa953903`: 277 have PL rate evidence and 27 translated evidence. This is
not evidence that every squad player lacks history, but it makes universe-wide confidence
inappropriate. The same query found **zero stored reviews and zero lessons**. The reactive-learning
defect is therefore a confirmed latent behavior, **not an established cause of the latest plan**.
Do not attribute the user's disappointing decisions to a memory adjustment that was not active.
Query results are saved in [the data audit](../../artifacts/product-data-audit-20261021.json).

The saved [minutes holdout](../../artifacts/minutes-holdout-2025.json) reports training on 2024,
testing on 2025: 29,747 test rows; start Brier 0.07826 versus 0.10358 for its baseline. This is
encouraging evidence about start classification under that reconstruction. It is **not** an
evaluation of injury probabilities, final simulated minutes, assists, defensive contributions,
captaincy, transfers, subscriber outcomes, or the current complete production policy.

`README.md:110` contains stronger historical points/replay claims. Do not reuse those in a sales
page until their exact data, code revision, rules, policy, forecast cutoff and paired rows can be
reproduced. The existing study's two seasons cannot also become an untouched final test set after
using their results to choose changes. Reserve another eligible season or collect prospective data.

## Research implications

**Start with strong, simple challengers.** Daniel Groos's OpenFPL combines position-specific tree
ensembles and evaluates forecasts prospectively against simultaneous commercial predictions.
The paper's prospective sample is **2024/25 GW32–38**, not a whole unseen season. Its results make
a direct-points challenger worthwhile; they do not prove that a copied model will win this season.
Code licensing and underlying data rights are separate. Inspect feature timing and scoring changes
before any reuse. [Original paper](https://arxiv.org/html/2508.09992)

Robust optimization is not automatically better: the Ramezani/Dinh study reports mixed results
across objectives. Use it to design comparisons, not justify another layer of complexity by name.
Its study setup must be reconciled with actual transfer costs and information timing before
applying results here. [Original study](https://arxiv.org/html/2505.02170v3)

Assess probability distributions with proper scoring rules as well as mean error. Brier/log loss
for events and CRPS for points can distinguish honest uncertainty from confident errors. Prediction
intervals also need coverage and width diagnostics. These are methodological recommendations,
not accuracy already achieved by this project.
[Gneiting and Raftery](https://stat.uw.edu/research/tech-reports/strictly-proper-scoring-rules-prediction-and-estimation)

Congestion research supports investigating workload: a systematic review found increased match
injury incidence in five of eight included studies, with inconsistent findings for some other
outcomes. It does not provide a justified universal percentage penalty for an individual player's
next start. Use actual participation and rest as features, validate by cohort, and separate
observations from inferred fitness. [Review](https://pmc.ncbi.nlm.nih.gov/articles/PMC9758680/)

## Proposed prediction architecture

Retain the Python engine and modular components. Add a stable forecast contract before replacing
algorithms. Every forecast needs a data snapshot ID, model/config version, rules version, seed,
decision cutoff, feature provenance and eligibility status. Persist both raw and actually used
forecasts; currently `agent.py:312` saves raw forecasts before decision-only adjustments.

1. **Reliable inputs.** Canonical player identities with club/season validity; quarantined ambiguous
   joins; complete fixture coverage; exact deadlines; separate `occurred_at`, `published_at`,
   `observed_at` and correction timestamps. Missing xA or defensive events remain unknown.
2. **Availability and minutes.** Distinguish unavailable, available-but-not-selected, starter and
   substitute states. Estimate probability of selection conditional on availability, and a duration
   distribution conditional on role. Construct legal joint lineups using club depth and positional
   constraints; represent missing squad members explicitly. Respect substitutes, red cards and
   minutes accounting. Do not normalize a weakly observed squad into false certainty.
3. **Football rates.** Hierarchical, recency-weighted non-penalty xG/xA, set-piece roles and defensive
   involvement, shrunk by exposure and evidence quality. Estimate penalties separately only when
   inputs separate penalty xG. Model promotion/league changes with uncertainty, not arbitrary fixed
   confidence. Fit assists, saves, defensive contributions and bonus on their own scoring semantics.
4. **Match context.** Shared team attacking/defensive strength and home advantage; optional licensed
   market information with recorded observation times. Retain shared clean-sheet/goal correlation.
   Cross-competition workload includes actual minutes, recovery hours and recent return from absence;
   an upcoming cup game is scheduling context, not evidence of current fatigue.
5. **Challenger and calibration.** Compare existing mechanistic forecasts, simple recency baselines,
   and a direct-points tree model using the same eligible inputs. Blend only on development data.
   Check both mean bias and event/distribution calibration after every transformation.
6. **Decision policy.** Optimize the user's actual confirmed state; keep forecasts independent of
   the user's rank preferences. Evaluate final plans with one shared scoring implementation.
   Rank-risk adjustments belong in explicit utility, not unexplained extra player points.

### Injury intelligence throughout the week

The desired Haaland behavior is a general rule: an owned player with a 75% doubt remains eligible
to start. Compare the starter-plus-legal-cover distribution with alternatives; account for a cameo
preventing a substitution and the rare card-only participation case. Do not hardcode Haaland or
assume the flag is a calibrated 75% probability of starting.

Proposed evidence record:

```text
player_id, club_id, fixture_id, source_url, publisher, source_class,
published_at, observed_at, event_at, short_permitted_excerpt,
claim_type, speaker, match_scope, extraction_confidence,
supersedes_id, conflict_state, reviewer, expires_at
```

Claim types: confirmed out; suspension; training return; individual training; assessment pending;
available for selection; manager rotation comment; confirmed lineup; actual minutes. Training,
availability and starting are different statements. An official club's qualified fitness updates
illustrate why extraction must preserve that distinction.
[City example](https://www.mancity.com/news/mens/pep-guardiola-liverpool-injury-news-63905976)

Monitor the whole current player pool, rather than only already-flagged players. Maintain coverage
and last successful checks for all 20 clubs. Prefer official club/competition publications and
attributed press conferences; add reliable reporting with clear provenance. Syndicated copies
are one underlying claim, not independent confirmations. Extract only as permitted by source terms.

Proposed schedule: post-match ingestion; daily evidence updates; extra checks after European games
and press conferences; deadline-relative checks at T-24h, T-6h and T-60m when provider limits allow.
Use a hosted scheduler later: local files cannot keep monitoring while the laptop sleeps.
Missing or blocked coverage must remain visible. Conflicts and undated claims cannot silently clear
an injury. An LLM can extract structured claims with citations; it must not invent medical facts or
probabilities. Treat retrieved text as untrusted input, with no execution tools or credentials.

### A defensible value-player feature

The opportunity is early identification of **sustainable role changes at an affordable price**:
new starter, genuine set-piece responsibility, tactical role change, replacement opportunity,
improved fixtures or defensive-contribution potential. Low ownership alone is not predictive edge.

Rank candidates by expected **squad improvement**, after minutes, hits, selling prices, alternative
uses of cash, bench coverage and transfer flexibility. Show price, observed ownership, projected
gain over the actual outgoing player, minutes distribution, evidence strength, downside and the
news that would invalidate the idea. Separate research watchlist from actionable recommendations.
Prior-only players may remain exploratory candidates but cannot receive a high-confidence buy label.

Validate a fixed weekly shortlist created before deadlines against price/position-matched
alternatives over predeclared 1/4/6-GW horizons. Count every selection, failed pick and retained
non-recommendation; report net points and adoption costs. Avoid retrospective lists of players who
later became successful. Analyze low ownership separately from genuine forecast improvement.

## Experiment protocol and promotion gates

Freeze this protocol before tuning. These are proposed release criteria, not completed results.

| Experiment | Comparison | Required evidence |
|---|---|---|
| E0: integrity | Old/new simulator and rules | Legal lineup/participation invariants; unavailable-player exclusion; preservation or measured recalibration of marginals; all synthetic defects reproduced then corrected. |
| E1: minutes | Current overlay vs shared-state model; last-start and recent-start baselines | Brier/log loss for start, appearance, 60+; minutes MAE; calibration plots by flag/position/role, including final sampler output. |
| E2: points | Current model, simple form, direct-points challenger, optional permitted benchmark | Paired MAE/RMSE/CRPS/bias by position, price, prior coverage, predicted-minute band, and horizon; uncertainty intervals. |
| E3: policies | Neutral expected points; current ownership/rank/memory rules; evidence-gated alternatives | Same initial squads and information, legal sequential transfers, hits, bank, chips and autosubs; total net points, drawdowns, turnover, realized regret and sensitivity. |
| E4: news/workload | E2/E3 with and without each new input | Feature ablations; stale/conflicting-source tests; benefit in rotation/doubt cohorts without material overall regression. |
| E5: value scouting | Frozen shortlist vs matched alternatives | Predeclared selection universe; prospective net gains and failure rate; evidence coverage and multiple-comparison controls. |

Use rolling-origin splits, a separate tuning window, and a locked final period. Reconstruct only
what was knowable then. Mark incomplete historical injury/news archives ineligible for testing
news-aware claims. Historical scoring changes need explicit rule versions; do not treat unavailable
DefCon history as zero. Contemporary licensed benchmark forecasts must be captured at matching
cutoffs, with permission; never compare hindsight reconstructions with live forecasts as equals.

Use common simulated worlds for paired plan comparisons, but independent evaluation seeds for
candidate reranking to reduce simulation selection bias. Quantify Monte Carlo error and parameter
uncertainty separately from match randomness. Bootstrap by gameweek/match blocks, not thousands
of nominally independent players. Overlapping squads are correlated; 100 teams sharing players
do not create 100 independent seasons of evidence.

Proposed promotion rule: all integrity gates pass; primary held-out loss improves and a paired
95% interval supports that improvement before claiming superiority. Predeclare a 1% relative
non-inferiority margin for essential large cohorts; use interval-based assessment and report
underpowered groups as inconclusive. If this blocks promotion, collect more data or keep the
simpler model; do not loosen the rule after seeing results. A research feature can remain visibly
experimental without advertising accuracy gains.

Run 8–12 prospective gameweeks as an initial operational/decision study; this is not automatically
enough to establish superiority. Track every recommendation, including abstentions. Review legal
errors, data outages and unstable advice immediately. Repeated losses alone do not justify model
retuning. Publish a versioned performance page with sample counts, exclusions, uncertainty and
negative results. Never promise guaranteed points, a top rank or mini-league victory.

## Decisions to avoid

Do not start with reinforcement learning, an LLM choosing transfers, player-name exceptions,
continuous tuning to one manager's disappointments, or an expensive event-data contract before
verifying the needed fields and rights. Do not add a differential bonus to predicted points or
claim the architecture itself is unique. The possible advantage is the quality of the evidence,
validated decisions and user experience accumulated over time.
