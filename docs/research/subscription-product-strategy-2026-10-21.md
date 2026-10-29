# From local FPL agent to a subscription product

21 October 2026. Recommendation: **build a private, read-only decision assistant first; do not
launch paid public access to the current local dashboard.** The engine has reusable foundations,
but customer isolation, trustworthy evaluation, commercial data rights and a focused customer
experience are release blockers. The [model audit](model-reliability-2026-10-21.md) documents
reproduced defects and proposed experiments. [TASK.md](../../TASK.md) is the implementation handoff.

Research used the Exa Search skill: 19 searches across five workstreams, requesting 95 result
slots, yielding 93 distinct candidate URLs; 22 distinct page URLs were attempted for deeper
retrieval. These are research activity counts, not 95 independently verified sources. Selected
primary-source extracts informed this report. One incorrect provider terms URL failed and was
resolved to its actual terms page. The [source manifest](../../exa-results/fpl-product-strategy-2026-10-21/source-manifest.json)
records queries, URLs and exclusions. No customer interviews, paid data trial, vendor negotiations
or independent reproduction of competitor performance were conducted.

## The product to test

Initial customer hypothesis: a serious but time-constrained FPL manager who wants a dependable
weekly decision in five minutes, understands that predictions can lose, and wants useful alternatives
without learning optimization settings. This is narrower than serving casual fans, creators,
professional analysts and mini-league administrators simultaneously.

Proposed promise: **Understand your best move, the evidence behind it, and what could change it.**
Three recurring reasons to return:

- A squad-specific decision brief: roll or transfer, captain/vice, bench order and a small number of alternatives.
- Availability monitoring: what changed since the last plan, whether it affects this squad, and when it was checked.
- Value discovery: affordable role changes and credible alternatives, with reasons to wait or reject them.

The distinction is a hypothesis, not a proven market gap or an exclusive invention. Establish
trust through stable advice, transparent uncertainty and a verifiable record. The first useful
output must work without a subscription or an FPL password.

## Competition and pricing evidence

Prices below were visible in retrieved official pages on the research date. Promotions, geography,
tax and checkout conditions can differ. Vendor success claims are marketing, not independently
verified evidence that this project can match them.

| Product | Verified offering | Retrieved price evidence | Implication |
|---|---|---|---|
| FPL Review | Projections, multiweek optimization, sensitivity controls; documentation describes hourly refresh and press-conference information. | Patreon extract displays $5 and $8.50 but does not reliably associate them with tiers/billing; exact payable plan unverified. | A solver plus injury news is already an established offering. |
| Fantasy Football Hub | Starter includes team/transfer/chip planning; Pro adds Opta stats, predictions and comparisons; expert services also marketed. | No reliable amount extracted. Support describes monthly/annual tiers, while one campaign page says annual-only. Verify the specific offer rather than reconcile by guessing. | Competing on breadth means competing with content and community too. |
| Fantasy Football Scout | Projections, statistics, planners, comparisons and editorial content. | Chief Scout £10/month; first-month £5 promotion; £50 annual offer. | A £49 annual product needs a clear, narrow reason to choose it. |
| Fantasy Football Fix | Transfer/rotation planning, forecasts, stats, and higher-tier expert features. | Annual page displays promotional £3.48/month Premium and £3.98/month higher tier, billed yearly. | Compare annual commitments with annual commitments; avoid an unjustified premium. |

Sources: [Review features](https://docs.fplreview.com/getting-started/premium-features/),
[Review model](https://docs.fplreview.com/the-model/projections/massive-data-model/),
[Review Patreon](https://www.patreon.com/fplreview/membership),
[Hub tiers](https://fantasyfootballhub.freshdesk.com/support/solutions/articles/103000113576-what-are-the-differences-between-tiers-),
[Hub campaign](https://www.fantasyfootballhub.co.uk/landing/LTFPL),
[Scout pricing](https://www.fantasyfootballscout.co.uk/pricing/),
[Fix pricing](https://www.fantasyfootballfix.com/premium/).

## Why the present interface does not feel like a finished product

I started the existing localhost dashboard, waited for data, captured and opened real screenshots:
[desktop overview](../../artifacts/product-audit-overview-desktop-loaded.png) and
[mobile team](../../artifacts/product-audit-team-mobile-loaded.png). The initial loading-state
captures are also retained; the design conclusions use the loaded captures. Browser console:
zero reported errors/warnings during these inspected routes. This is a targeted audit, not a new
complete browser regression run.

The design has consistent dark colors and spacing, but the information hierarchy is wrong for a
paying manager. The overview prioritizes a slogan, player-universe count and stored-decision count.
Its primary action opens operations. Account points, rank and value are unavailable. The actual
weekly decision is not the first thing the user understands.

The mobile pitch compresses five player cards into one row: truncated names, tiny club/price/range
labels and identical shirts make it hard to identify players. The horizontally scrolling eight-item
navigation hides destinations. Large technical caveats appear before or beside the useful answer.
The team page explicitly cannot identify whose stored decision it is. Those are product-design and
data-contract problems; swapping CSS frameworks alone will not solve them.

### Proposed design brief

Use a restrained football editorial style: charcoal surfaces, off-white text, one muted green
accent, flat borders, and strong numerical typography. Use licensed/self-hosted typography if
needed, a consistent SVG icon set, and a small token system. Reserve colors for meaning. The
working name "Matchroom" has not been cleared for trademarks or domains.

Navigation: **This Week / Squad / Discover / Plan**, with account settings separate. Operations,
ingestion logs and raw data health become a restricted admin workspace. Customers still see a
clear freshness or missing-data notice wherever it changes a decision.

Desktop first screen: team identity and exact deadline; the recommended decision and its status;
expected improvement versus holding with uncertainty; the two most important reasons; compact
squad; one obvious action to compare alternatives. Secondary information explains news changes
and risks. A missing input produces a direct correction step, not a wall of unavailable KPIs.

Mobile: bottom navigation, one-column decision brief, a compact pitch using recognizable display
names and one key metric. Tap opens a readable player sheet with minutes, evidence and alternatives.
Provide an accessible list view for the squad. Do not make small, dense cards carry six metrics.
Use original neutral graphics until actual image/logo rights are secured.

Suggested design tokens: 4/8px spacing rhythm; 12/14/16/20/28/36px type scale; 16px body text;
12px minimum supporting labels where practical; 44px product target for primary touch controls.
Target WCAG 2.2 AA, with measured contrast, visible keyboard focus and screen-reader labels.
WCAG's AA minimum target criterion is 24 CSS pixels with exceptions; 44px is our more generous
design target, not a claim about that AA requirement.
[W3C target-size guidance](https://www.w3.org/WAI/WCAG22/Understanding/target-size-minimum.html),
[WCAG 2.2](https://www.w3.org/TR/WCAG22/).

Validate the prototype with five prospective users: at least four should identify the suggested
action, its main risk and data timestamp within 30 seconds, then compare an alternative without
help. Proposed usability gate, not research already performed. Inspect 360/390/768/1440px states,
keyboard navigation, long names, uncertain injuries, empty accounts, slow requests and stale plans.

## Support any team without rebuilding the forecasting engine

The core is not hardcoded to one squad: `data/entry.py:26` defines generic `SquadState`, and
`agent.py:213` accepts an entry ID. However, **accepting another entry is not multi-user support**.
`db/schema.py` decisions/reviews/lessons have no customer key; `dashboard/service.py:137` selects
global latest decisions; `dashboard/jobs.py:119` uses one preferences file and shared plans directory.
`lessons.py:352` replaces season/GW lessons without entry identity. A customer's history could affect
another customer's plan if this were exposed as a shared service unchanged.

Separate three domains:

| Domain | Shared or private | Proposed contents |
|---|---|---|
| Football data and forecast versions | Shared, subject to data rights | Canonical players, fixture/news observations, immutable forecasts and validation status. |
| Manager state | Private to account/workspace | Entry linkage, user-confirmed squad, buy/sell prices, bank, FT, chips, preferences, timestamps and provenance. |
| Decisions and subscription | Private to account/workspace | Plans, overrides, review history, jobs, exports, entitlement state and audit trail. |

A public entry ID identifies a team, not its owner's identity or account authorization. Import
public last-deadline state and label it as such. Let the customer correct current transfers,
selling prices, bank, FT and chips; validate before solving. Handle GW1/late entrants, recent
Wildcard/Free Hit, missing history and previous-season IDs explicitly. Do not present inferred
purchase prices as confirmed current selling prices. Store the accepted state version with each plan.

Proposed production architecture: a typed web frontend (React/TypeScript is reasonable for a new
customer application), a thin authenticated Python API wrapping existing model/solver modules,
PostgreSQL for accounts and transactional manager state, and background workers for forecasts and
solves. Retain DuckDB for research/offline analytics. Start with a modular application and a simple
durable job queue; introduce Redis or additional services only after measurement justifies them.
Keep the local command and loopback admin tool separate. Do not publish the existing WSGI server
by changing its bind address.

Forecast once per shared data/model version; solve against each private squad state. Cache keys
include tenant, squad hash, forecast version, rules, objective and constraints. Enforce isolation
on APIs, database access, caches, workers, object storage and exports. Use server-verified identity,
not a trusted request parameter. Test two-account cross-access failures systematically.
[OWASP multitenant guidance](https://cheatsheetseries.owasp.org/cheatsheets/Multi_Tenant_Security_Cheat_Sheet.html)

Proposed latency targets: cached brief p95 under 2 seconds; ordinary queued solve p95 under 30
seconds at an explicitly tested load; long chip analysis shows progress and can time out gracefully.
Current plan latency is not proof these targets are achievable. Profile ingestion, forecast and
solver separately; impose quotas, concurrency caps, deduplication and cancellation. An illustration:
1,000 users making four 360-second jobs would consume 400 serialized worker-hours per gameweek.
That is arithmetic, not a CPU/cloud bill measurement. Shared forecasts help, but per-user solve
cost still needs measurement.

## Commercial data and launch constraints

FotMob's own terms prohibit automated retrieval and reuse without written consent, and limit the
ordinary software license to personal noncommercial use. This makes its current feed an unresolved
commercial dependency, not a free production entitlement. No permission has been established by
this research. Do not expand collection for the subscription launch while assuming permission.
[FotMob terms](https://www.fotmob.com/tos.txt)

Premier League website terms also reserve commercial reuse and database rights; club/league marks
require separate permission. Public endpoint access and a GitHub archive's code license do not
establish commercial rights to underlying data. Obtain use-case-specific advice and permissions.
[Premier League terms](https://www.premierleague.com/en/terms-and-conditions)

| Candidate | Evidence | Procurement questions before adoption |
|---|---|---|
| Sportmonks | Published core plans start at €29/month; xG and historical coverage need field/plan checking. Terms allow building commercial products, but restrict raw resale and separate image rights. Provider explicitly says it is not the competitions' official rights holder. | EPL and cup coverage; player xA/DefCon-equivalent events; observation timestamps; corrections; model training and retained derived outputs; publication/caching; domain limits; total add-on price. |
| Stats Perform / Opta | Supports tailored competition/data licensing; pricing is quote-based. | Same field/rights checklist; startup minimums; historical depth; SLA; model-training and derived-prediction rights. |
| Existing FPL/archive/news inputs | Useful prototype inputs; commercial permission is not established here. | Written provenance and rights matrix; access terms; permitted excerpt/link use; retention and deletion requirements. |

Sources: [Sportmonks pricing](https://www.sportmonks.com/football-api/plans-pricing/),
[xG coverage](https://www.sportmonks.com/football-api/xg-data/),
[Sportmonks terms](https://www.sportmonks.com/terms-of-service/),
[rights clarification](https://www.sportmonks.com/integrity-support/),
[Opta pricing/licensing](https://www.statsperform.com/faqs/stats-perform-faqs-pricing-licensing/).
No supplier has been contacted and no subscription has been purchased. Provider list prices are
not quotes for this product's complete requirements. Do not assume licensing statistics also licenses
logos, headshots, article text or prediction exports.

Privacy, tax and consumer obligations depend on where the business and customers are located;
Dubai timezone alone does not establish jurisdiction. Plan minimal personal data, account deletion,
retention rules, vendor agreements and clear renewal/cancellation terms. Have the chosen markets
reviewed before taking payment. The ICO describes privacy as a lifecycle design responsibility,
not a final privacy-policy page. [ICO guidance](https://ico.org.uk/privacy-design)

## Monetization experiment and economics

Start with one paid tier, not a complicated tier matrix. Suggested **test prices**, not validated
demand: £6.99/month or £49/year. Annual billing must clearly state renewal timing and off-season
coverage; do not describe a nonrenewing season pass as monthly recurring revenue. Free access can
offer a limited team check and public methodology; paid access offers saved scenarios, weekly
alternatives and squad-relevant updates. Basic correctness and source honesty belong in every tier.
Do not offer unlimited expensive solves, a lifetime plan, a rank guarantee or paid auto-transfers.

Illustrative unit economics, all assumptions except arithmetic: UK-like 20% VAT-inclusive pricing,
processing at 3% + £0.25 per charge, £0.60 variable cost per active subscriber-month, fixed operating
cost scenarios £350 or £1,200/month. These are **not** actual tax obligations, processor quotes,
vendor quotes or measured workload costs. Customer acquisition, refunds and founder pay are excluded.

```text
monthly contribution = price / (1 + assumed tax rate)
                       - processing fee - variable servicing cost
annual contribution  = annual price / 12 / (1 + assumed tax rate)
                       - annual processing fee / 12 - monthly variable cost
break-even subscribers = ceiling(fixed monthly cost / monthly contribution)
```

| Plan | Approx. contribution/customer/month | Break even at £350 fixed | At £1,200 fixed | At £3,350 fixed, including illustrative £3,000 founder pay |
|---|---:|---:|---:|---:|
| £6.99 monthly | £4.77 | 74 | 252 | 703 |
| £49 annual | £2.66 | 132 | 452 | 1,260 |

Annual cash arrives upfront but must support a year's service. At 100 monthly subscribers gross
receipts are £699/month, not a sustainable full-time salary under these assumptions. License
minimums, human news review, refunds or acquisition costs can materially worsen the picture.
Use cohort retention across deadlines and seasons; do not project 12 months of summer retention
from in-season usage. Do not calculate confident lifetime value before observing renewal/churn.

Before substantial spending: interview 10–15 target managers about their current weekly workflow,
existing subscriptions and last painful decision. Test two value propositions with a clickable
prototype. Recruit 20–30 consenting private-beta users spanning different squad/rank/chip states.
Proposed discovery gates: 70% complete a verified team import; 50% of activated users return for
four successive deadlines; at least 10 beta users explicitly commit to the tested price. These
small samples are directional business signals, not statistically precise market estimates.

Acquire early users through useful, timestamped public analysis and permitted community participation.
Show failed value picks and corrected forecasts alongside successes. Test creator partnerships only
with consent, disclosed terms and measured paid conversion. Do not buy ads until activation,
repeat usage and contribution economics are understood. No outreach is authorized by this document.

Billing implementation should use hosted checkout/customer portal, verified webhooks, idempotent
event handling, entitlement reconciliation and tests for renewals, failed payments and cancellation.
A browser payment-success URL must not grant access by itself.
[Stripe subscription guidance](https://docs.stripe.com/billing/subscriptions/webhooks),
[webhook guidance](https://docs.stripe.com/webhooks).

## Delivery sequence and go/no-go decisions

1. **Weeks 1–2, indicative:** reproduce/repair P0 model defects; freeze evaluation protocol; begin
   rights discovery and customer interviews. No new accuracy marketing or paid launch.
2. **Weeks 2–4:** versioned data/manager contracts; common-row backtests; complete team import;
   customer prototype and usability rounds. Reuse the existing engine.
3. **Weeks 4–8:** isolated accounts, shared forecasts, private plans, bounded jobs and the focused
   customer UI; licensed data path; all-club coverage or clearly narrower declared coverage.
4. **Weeks 8–12+:** prospective beta, operational load/restore drills, policy ablations and pricing
   validation. Shadow evaluation continues for as many gameweeks as evidence requires.
5. **Paid launch:** only after rights, isolation, correctness, usability and billing gates pass.
   Forecast-superiority claims require separate statistical evidence; shipping software does not
   establish them. If customers value the workflow but predictive superiority is unproven, sell
   honest decision support without claiming an unbeatable algorithm.

These are planning ranges for a focused team, not a delivery commitment. Licensing delays,
historical coverage and elapsed gameweeks can extend them. Defer native apps, multiple fantasy
games, automated FPL execution, live chat, public raw-data APIs and elaborate rank-risk modes.
The next concrete milestone is a reliable weekly brief for several unrelated squads, backed by
reproducible forecasts and a prototype that users can understand quickly.
