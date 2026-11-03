# Football intelligence and the weekly learning loop

This page covers four things: what the agent reads besides FPL's own data, how that text becomes
evidence a forecast can use, which of it changes the plan today, and how the agent decides,
week by week, whether a change has earned its place.

## What is active and what is shadow-only

| Piece | Status by default | Changes the plan? |
|---|---|---|
| Pre-deadline review, cutoff recording, final-only calibration and lessons | **active** | Yes. Calibration and lessons now learn from final, eligible reviews only. |
| News ingestion (RSS) | **active** in the daily data workflow | No, it only stores items and claims. |
| X ingestion | **configured but idle**: no accounts are listed and `X_BEARER_TOKEN` is not set | No. |
| News availability overlay (`FEATURE_NEWS_AVAILABILITY`) | **shadow** | No. A second forecast with it is stored and scored. |
| Rest and workload overlay (`FEATURE_WORKLOAD`) | **shadow** | No. Same as above. |
| Learning state (`fpl-agent learn`) | **active** in the review workflow | Only through calibration factors and feature promotions that passed their thresholds. Until a state exists, the plan behaves as before. |

Set `FEATURE_<NAME>=off|shadow|on` (as an environment variable or repository variable) to
override. An environment value always wins over the learning state. Live FPL execution, the
rails and the deployment settings are unchanged: overlays only change the probabilities the
simulator uses, and FPL's own status flags still decide hard eligibility.

## Point in time: deadlines, cutoffs, finality

* Every `db-update` (and `lessons`) records each gameweek's FPL `deadline_time`, `finished` and
  `data_checked` in `fpl_events`.
* Every stored projection run is registered in `forecast_runs`: `created_at`, the information
  `cutoff` it used, the first gameweek's `deadline` (and whether that came from FPL or was
  estimated as 90 minutes before the first kickoff), `eligible` (made before the deadline),
  `variant` (`primary` = what the plan used, `shadow` = the other arm), the parent run, the model
  and feature versions, the feature flags and calibration it used, and any point-in-time
  limitations of its inputs.
* A review scores **one** run: the newest eligible primary run. It never mixes runs per player,
  and a run made after kickoff can never be scored. Projections saved before this change have
  no registry row; they are grouped by run id and judged against the deadline the same way
  (`legacy: true` in the report).
* A gameweek is **final** when FPL marks it `finished` and `data_checked` and its points are
  loaded. Provisional reviews are stored with `final: false`; provisional lessons are reported
  with a PROVISIONAL header and **not stored**. Calibration, the template correction and the
  learning state ignore anything that is not final and eligible.
* A player in the forecast with no official result row is **left out** of the review and
  counted in `missing_actuals`. It is never scored as zero.
* A double gameweek's actuals sum both fixtures, and the scoring components come from both
  fixtures' `explain` breakdowns.

What each input of a projection may see at cutoff `c`:

| Input | Rule |
|---|---|
| Squad snapshot (status, chance, price) | `snapshot_at <= c` |
| Bookmaker prices | pre-match prices with `ingested_at <= c`; closing prices never before kickoff |
| Matches and team strength | matches before the cutoff's date |
| Player history and minutes model | gameweeks before the one being forecast |
| Workload | appearances whose kickoff + 3 h <= c |
| News evidence | claims first observed (not published) at or before `c`, newest revision, not expired |

**Historical backfills are not point-in-time**, and backtests report this under `limitations`:

* vaastav archive squad rows are stamped at kickoff and carry no availability. A backtest uses
  them (prices at kickoff, everyone assumed available) and says so.
* Pre-match odds loaded by a later backfill have an unknown publication time. They are used only
  when no point-in-time price exists. Closing prices are never used before kickoff.
* There is no historical news. News features are evaluated forward only.

## Sources

The registry is `config/news_sources.yaml` (override with `NEWS_SOURCES_FILE`). Each source has
an `id`, a `kind` (`rss` or `x`), a `tier` (1 official club/manager channel, 2 established outlet
or named journalist, 3 other), `min_interval_minutes` and `max_items`.

| Source | Domain(s) | Credential | Verified |
|---|---|---|---|
| BBC Sport football RSS | `feeds.bbci.co.uk` | none | Live on 3 Nov 2026: 60 + 45 items, parsed, deduplicated, one claim resolved (William Saliba back in training) |
| X user timelines (API v2) | `api.x.com` | `X_BEARER_TOKEN` (app-only bearer token, developer.x.com) | **Live ingestion not verified**: no token was available. The endpoint answered 401 without one. The contract (user lookup, `since_id`, `pagination_token`, edits, reposts, 429 and `x-rate-limit-reset`) is covered by mocked tests. |
| LLM claim extraction (optional) | `api.anthropic.com` | `ANTHROPIC_API_KEY` and `NEWS_LLM=1` | Mocked tests only. |

The Guardian's football RSS answered with a bot-check HTML page from the test environment. That
page is kept as a recorded fixture of a malformed feed and is not in the registry.

To read X accounts, list them under `x-clubs` (tier 1) or `x-journalists` (tier 2) and add the
`X_BEARER_TOKEN` secret. Nothing is read from X until you do both.

### Storage and provenance

| Table | What |
|---|---|
| `news_items` | One row per source item: source id, provider and provider id (RSS guid, the original X post id), canonical URL (tracking parameters removed), author, the source's `published_at`, our `first_seen_at` (never changes) and `retrieved_at`, newest `content_hash`/title/content and `revision`, `story_key`, `repost_of`, source tier, raw metadata |
| `news_revisions` | Every version of an item's text with its `observed_at`: an edit adds a revision and never overwrites one |
| `news_entities` | Player mentions per revision: resolved (player id, confidence) or ambiguous (candidate ids) |
| `evidence_claims` | Structured claims (below) |
| `source_state` | Per source: cursor (ETag, Last-Modified, X `since_id` per account), last attempt and success, last error, consecutive failures, `next_allowed_at` after a rate limit, item count |
| `source_runs` | One row per attempt: status, requests, fetched, new, revised, unchanged |

* **Idempotent.** Item id = hash(source id, provider id). A refetch of unchanged content only
  touches `retrieved_at`. Repeated runs within `min_interval_minutes` make no request.
* **Incremental and bounded.** Conditional GET for RSS. `since_id` plus at most `max_pages`
  pages of `max_items` per X account. A 2 MB response cap and a 20 s timeout. A 429 stores the
  server's reset time, and the source is skipped until then. Posts older than the newest page
  read are not backfilled.
* **Copies.** A repost or quote shares the original's `story_key`. So do the same canonical URL
  and near-identical text (4-word shingle Jaccard >= 0.6 within three days). Independent
  confirmation counts stories, not items.
* **Failure.** Every source is optional. A missing credential is `skipped`, an error is
  `failed`, and the next source runs. `fpl-agent sources` shows `healthy`, `stale` (no success
  for three intervals), `failed xN`, `rate_limited xN`, `skipped (...)`, `never fetched` or
  `disabled`.

## From text to evidence

`news/extract.py` turns each new or revised item into claims. Every claim keeps the supporting
clause (`quote`), item and revision ids, source id and tier, name-match confidence,
`certainty` (`confirmed` or `speculative`), `effective_at` (when this revision was first
observed by us), `expires_at`, an optional `return_at`, the `extractor` and its version.

| kind | values | expires after |
|---|---|---|
| `availability` | `out`, `doubtful`, `fit` | 7 / 4 / 5 days |
| `training` | `full`, `partial` | 4 / 3 days |
| `return` | `weeks:<n>`, with `return_at` | return date + 3 days |
| `selection` | `rotation_risk`, `expected_start`, `benched` | 4 days (stored, not used by a feature yet) |
| `set_piece` | `penalties`, `set_pieces` | 60 days (stored, not used by a feature yet) |

* **Names.** Only players in the newest FPL squad snapshot can be resolved. A full name gives
  0.95. A surname or FPL short name gives 0.8 when exactly one squad player carries it, or 0.7
  when the text also names his club and that leaves one. Anything else stays *ambiguous*, is
  stored with its candidates, and never changes a forecast.
* **Confirmed or speculative.** Wording such as "ruled out" or "will miss" with no hedge is
  confirmed. Any speculation marker ("could", "reportedly", "expected to", "fears") makes the
  clause speculative. Negations ("not ruled out") make no claim.
* **Extractors.** The rule extractor is the default and the fallback. The LLM extractor (the
  scout's API call) is optional. It treats fetched text as data and its output is validated:
  known kinds and values only, the quote must be a verbatim substring of the item, and the
  player must be named in the quote and resolve through our resolver. Any failure falls back to
  the rules for that batch.

## Feature definitions

### News availability (`model/evidence.py`, overlay in `model/overlays.py`)

* **Unit:** the availability factor (probability of being available, 0 to 1) that multiplies
  P(start) and P(cameo).
* **Eligible claims at cutoff `c`:** resolved player with confidence >= 0.7; kind availability,
  training or return; `effective_at <= c`; from the newest revision observed by `c`;
  `expires_at > c`.
* **Signal:** out 0.0, doubtful 0.5, fit 0.95, training full 0.9, training partial 0.6, and 0
  before a reported return date.
* **Weight:** tier (1.0, 0.7, 0.4) x certainty (1.0 confirmed, 0.4 speculative) x name
  confidence. The newest story decides. An agreeing independent story adds half its weight.
  Confirmed stories within 48 h that disagree by >= 0.5 are a conflict and change nothing.
* **Effect:** shift = weight x (signal - FPL availability), capped at +-0.30, halved for each
  gameweek beyond the next. Never applied to FPL status `n`/`u`.
* **Missing:** no eligible claim means the forecast is unchanged.
* **Training:** none. No historical news exists, so this is a fixed, explainable overlay that
  is evaluated forward in shadow mode.
* **Explanation:** each changed player-week stores the claims (id, source, tier, certainty,
  time seen, quote) in the projection's `detail.evidence`. The plan report gets an `evidence:`
  line, and `fpl-agent explain` prints them.

### Rest and workload (`model/workload.py`)

| Feature | Unit | Unknown (NaN) when |
|---|---|---|
| `xc_rest_days` | days since the last appearance with minutes, any competition | none in the previous 60 days |
| `xc_mins_7d`, `xc_mins_14d` | minutes in the previous 7 / 14 days | the player has no appearance rows in the previous 365 days |
| `xc_team_matches_7d` | club matches in the previous 7 days | the club has no matches in the previous 60 days |

* **Cutoff:** facts of a match exist from kickoff + 3 h. At inference the run's cutoff applies
  as well. Training rows use only matches before their own kickoff.
* **Sources:** `appearances` and `matches` (FotMob, 15 competitions including UCL, UEL and
  UECL). Domestic cups are not in the database.
* **Training and inference:** a second gradient-boosted P(start) classifier is fitted on the
  baseline features plus these. NaN is kept as missing, never set to zero. The overlay moves
  next-gameweek P(start) toward it by at most +-0.10, and a player with no workload coverage
  keeps the baseline.

## Collection schedule and expected cost

| What | When | Requests or cost |
|---|---|---|
| RSS feeds | daily and weekly data runs (`data.yml`) | 2 requests a run, about 100 KB, free |
| X timelines | the same runs, once you list accounts and set the token | 1 lookup per account (once), then 1 request per account per run (up to `max_pages`). X bills pay-per-use per post read. Third-party sources put this at about $0.005 a post (not verified against X's own pricing page), so 10 accounts x 20 posts a day is about $1 a day. Lower `max_items` to cap it. |
| LLM extraction | only with `NEWS_LLM=1` | at most one call per source per run (first 15 new/revised items; the rest use the rules), about 6k input and 1k output tokens, a few cents a run |
| Shadow forecast | every plan run that has a shadow feature | next gameweek only, 500 samples, reuses the fitted models (about 10-20% extra run time) |
| Evaluation and learning | Tuesday review (`review.yml`) | local computation only |

## The weekly learning loop

Run by `review.yml` after the data update: `fpl-agent evaluate --gw 0` then
`fpl-agent learn --gw 0`.

1. **Score what was issued.** For the final gameweek, take the primary run and its shadow and
   score both on the identical cohort (players in both runs with a result). The metrics are
   points MAE, RMSE and bias; P(start) Brier, log loss and a 5-bin calibration table (players
   with one fixture, `starts` from FPL); P(play) Brier; evidence and workload coverage. The
   primary arm is also scored as issued (with the calibration factors the plan applied).
   Results go to `evaluations` and to `reviews` (kind `evaluation`).
2. **Candidate.** Calibration factors are re-estimated from final, eligible reviews. Each
   moves at most 0.03 from the current one, and nothing changes before two final reviews. For
   each feature, the paired forward evidence is the change in absolute error on player-weeks
   that feature changed (and no other feature did), across every evaluated final week.
3. **Thresholds, fixed in `learning.THRESHOLDS` before any evaluation:** a shadow feature is
   promoted to `on` only with >= 4 final gameweeks, >= 60 affected player-weeks, a mean
   improvement larger than 2 standard errors, and no worse P(start) Brier. A promoted feature
   whose error gets significantly worse goes back to `shadow`.
4. **Candidate vs current.** The candidate's calibration is validated on the newest final week,
   estimated *without* that week. It becomes current only if its holdout MAE is no more than
   0.01 worse. Otherwise it is stored as `rejected` with its reason, and the current state
   stays. With no proposed change, nothing is written, so a rerun is a no-op.
5. **Versioned state.** Each row of `learning_state` has its version, parent, status (`current`,
   `retired`, `rejected`, `rolled_back`), the gameweek it covers, the training cutoff, the config
   (calibration and feature modes), sample counts and the full evaluation, thresholds included.
6. **Rollback.** `fpl-agent learn --rollback` makes the parent version current again, or the
   built-in defaults if there is none. `--rollback --to N` restores version N.
   `fpl-agent learn --history` lists every version.

Hindsight lessons about transfers (`lessons.py`) are kept apart. They tune the planner's
memory switches as before, but they never count as evidence that a prediction feature works.

**Historical walk-forward** (`fpl-agent walk-forward --season 2025 --from-gw 6 --to-gw 38`) is
the chronological check for the workload feature, which does have history. Models are refitted
on rows before each gameweek. The first half of the gameweeks chooses the bound (0.05, 0.10 or
0.15). The second half is the final test, scored once with that bound. It also runs feature
ablations (all workload features, rest only, no club congestion). It is minutes-level (P(start)
Brier and log loss). Points-level comparisons need `backtest`.

## Commands

| Command | What |
|---|---|
| `news-ingest [--sources FILE] [--only ids] [--force]` | fetch due sources, store items, revisions, entities and claims |
| `sources` | health of every configured source |
| `features` | the feature modes in force and where each comes from (env, learning state or default) |
| `evaluate --gw N` | score the issued forecasts of a final gameweek |
| `learn --gw N`, `learn --rollback [--to V]`, `learn --history` | weekly update, rollback, version list |
| `walk-forward --season S --from-gw A --to-gw B` | historical chronological evaluation of the workload feature |

## Schema changes (additive)

New tables: `fpl_events`, `forecast_runs`, `news_items`, `news_revisions`, `news_entities`,
`evidence_claims`, `source_state`, `source_runs`, `evaluations`, `learning_state` and
`schema_migrations`. One column was added: `projections.variant` (migration
`2026-11-projection-variant`, `ADD COLUMN IF NOT EXISTS`). An older DuckDB file upgrades when
it is opened. Parquet import now matches columns by name, so an older export still restores
(new columns stay NULL, missing tables stay empty). Every new table is exported with the rest.
